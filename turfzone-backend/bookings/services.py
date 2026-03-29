"""
Booking confirmation service — single source of truth for the confirm flow.

Used by:
  - BookingViewSet.confirm  (direct confirm endpoint)
  - payments/views.verify_payment  (post-Razorpay confirmation)

Replaces the old TestRequestFactory hack in verify_payment.
"""

import uuid
import logging
from datetime import datetime
from decimal import Decimal

from django.db import transaction, IntegrityError
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import (
    Booking, BookingPreview, BookingSlot, BookingStatus, PaymentStatus,
)
from turfs.models import SlotMaster, BlockedSlot, TurfStatus

logger = logging.getLogger(__name__)

User = get_user_model()

# Re-use financial helpers from views (same module)
QUANTIZE_PLACES = Decimal('0.01')


def _quantize(val):
    return val.quantize(QUANTIZE_PLACES)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def confirm_booking(
    user,
    preview_token: str,
    client_total: Decimal,
    coupon_code: str = '',
    razorpay_order_id: str = None,
    razorpay_payment_id: str = None,
    razorpay_signature: str = None,
) -> dict:
    """
    Atomic booking confirmation.

    Returns dict with keys:
      success (bool), booking_id (int), message (str)
      OR: success (False), error (str), status_code (int)

    Raises nothing — all errors returned as dicts.
    """
    # Import here to avoid circular imports
    from .views import (
        _compute_slot_pricing, _compute_financial_breakdown,
        _create_ledger_entries, compute_slot_status,
        SLOT_STATUS_AVAILABLE, _STATUS_ERROR_CODE,
    )

    try:
        client_total = _quantize(Decimal(str(client_total)))
    except Exception:
        return {'success': False, 'error': 'total_payable must be a valid number', 'status_code': 400}

    try:
        with transaction.atomic():
            # ── 1. Lock and fetch preview ──
            try:
                preview = BookingPreview.objects.select_for_update().get(
                    preview_token=preview_token,
                    user=user,
                )
            except BookingPreview.DoesNotExist:
                return {'success': False, 'error': 'Invalid preview token', 'status_code': 404}

            if preview.is_used:
                return {'success': False, 'error': 'This preview token has already been used', 'status_code': 409}

            if preview.is_expired:
                return {'success': False, 'error': 'Preview token has expired. Please create a new preview.', 'status_code': 410}

            # ── 2. Re-check turf is still available ──
            turf = preview.turf
            if turf.status != TurfStatus.APPROVED or not turf.is_active:
                return {
                    'success': False,
                    'error': 'This turf is no longer available for booking',
                    'status_code': 400,
                }

            # ── 3. Lock slot rows ──
            slot_ids = [s['slot_id'] for s in preview.selected_slots]
            slots = SlotMaster.objects.select_for_update().filter(
                id__in=slot_ids, turf=turf,
            )
            if slots.count() != len(slot_ids):
                return {'success': False, 'error': 'One or more slots are no longer valid', 'status_code': 409}

            # ── 4. Re-check availability (race-safe) ──
            blocked_ids = set(
                BlockedSlot.objects.filter(turf=turf, date=preview.booking_date)
                .values_list('slot_master_id', flat=True)
            )
            existing_locks = set(
                BookingSlot.objects.select_for_update().filter(
                    slot_master__in=slot_ids,
                    booking_date=preview.booking_date,
                ).values_list('slot_master_id', flat=True)
            )
            for slot in slots:
                state = compute_slot_status(slot, preview.booking_date, existing_locks, blocked_ids)
                if state['status'] != SLOT_STATUS_AVAILABLE:
                    error_code = _STATUS_ERROR_CODE.get(state['status'], 'slot_unavailable')
                    return {
                        'success': False,
                        'error': f"Slot {slot.start_time.strftime('%H:%M')}-{slot.end_time.strftime('%H:%M')} is {state['status']}",
                        'error_code': error_code,
                        'status_code': 400,
                    }

            # ── 5. Recompute pricing ──
            slots_pricing = [_compute_slot_pricing(s, preview.booking_date) for s in slots.order_by('start_time')]
            financials = _compute_financial_breakdown(slots_pricing)

            # ── 6. SERVER-SIDE coupon validation (BUG-01) ──
            coupon_discount = Decimal('0.00')
            applied_coupon = None

            coupon_code = (coupon_code or '').strip().upper()
            if coupon_code:
                from users.models import PromoCode, CouponUsage
                try:
                    promo = PromoCode.objects.get(
                        code=coupon_code,
                        is_active=True,
                        valid_from__lte=timezone.now(),
                        valid_until__gte=timezone.now(),
                    )
                except PromoCode.DoesNotExist:
                    return {'success': False, 'error': 'Invalid or expired coupon code', 'status_code': 400}

                # Already used by this user?
                if CouponUsage.objects.filter(user=user, coupon=promo).exists():
                    return {'success': False, 'error': 'You have already used this coupon', 'status_code': 400}

                # Max uses reached?
                if promo.max_uses and promo.current_uses >= promo.max_uses:
                    return {'success': False, 'error': 'This coupon has reached its usage limit', 'status_code': 400}

                # Min order check
                if financials['subtotal'] < promo.min_order_value:
                    return {
                        'success': False,
                        'error': f'Minimum order of ₹{promo.min_order_value} required for this coupon',
                        'status_code': 400,
                    }

                # Compute discount server-side
                if promo.discount_type == 'percentage':
                    raw_discount = financials['subtotal'] * promo.discount_value / Decimal('100')
                else:
                    raw_discount = promo.discount_value

                # Cap at max_discount if set
                if promo.max_discount:
                    raw_discount = min(raw_discount, promo.max_discount)

                coupon_discount = _quantize(min(raw_discount, financials['subtotal']))
                applied_coupon = promo

            # ── 7. Re-verify first booking discount (BUG-10) ──
            user_locked = User.objects.select_for_update().get(pk=user.pk)
            first_booking_discount = Decimal('0.00')
            if user_locked.total_bookings == 0:
                # Only apply if preview already included it (prevents new discount injection)
                if preview.first_booking_discount > Decimal('0'):
                    first_booking_discount = preview.first_booking_discount

            # ── 8. Verify client total matches server authoritative total ──
            authoritative_total = _quantize(
                preview.total_payable - coupon_discount
            )
            if abs(authoritative_total - client_total) > Decimal('0.01'):
                return {
                    'success': False,
                    'error': 'Price has changed since preview. Please re-preview.',
                    'new_total_payable': str(authoritative_total),
                    'status_code': 409,
                }

            # ── 9. Determine booking time range ──
            start_time = min(s.start_time for s in slots)
            end_time = max(s.end_time for s in slots)

            # ── 10. Create Booking ──
            booking = Booking.objects.create(
                user=user,
                turf=turf,
                preview=preview,
                booking_date=preview.booking_date,
                start_time=start_time,
                end_time=end_time,
                selected_slots=slots_pricing,
                price_per_hour=Decimal('0.00'),
                total_price=financials['subtotal'] + financials['discount_total'],
                discount=financials['discount_total'],
                final_price=financials['subtotal'],
                gst_amount=financials['gst_amount'],
                commission=financials['commission'],
                gst_on_commission=financials['gst_on_commission'],
                commission_percent=financials['commission_percent'],
                platform_fee=financials['platform_fee'],
                gst_on_platform_fee=financials['gst_on_platform_fee'],
                platform_revenue=financials['platform_revenue'],
                owner_payout=financials['owner_payout'],
                booking_status=BookingStatus.CONFIRMED,
                payment_status=PaymentStatus.PAID,
                idempotency_key=uuid.uuid4(),
                applied_coupon=applied_coupon,
                coupon_discount=coupon_discount,
                razorpay_order_id=razorpay_order_id,
                razorpay_payment_id=razorpay_payment_id,
                razorpay_signature=razorpay_signature,
            )

            # ── 11. Create BookingSlot locks ──
            try:
                BookingSlot.objects.bulk_create([
                    BookingSlot(
                        booking=booking,
                        slot_master_id=sid,
                        booking_date=preview.booking_date,
                    )
                    for sid in slot_ids
                ])
            except IntegrityError:
                logger.warning(
                    f"IntegrityError: duplicate slot lock for turf={turf.id} "
                    f"date={preview.booking_date} slots={slot_ids}"
                )
                raise  # Triggers atomic rollback

            # ── 12. Ledger entries ──
            _create_ledger_entries(booking, financials)

            # ── 13. Mark preview used ──
            preview.is_used = True
            preview.save()

            # ── 14. Update user stats ──
            user_locked.total_bookings += 1
            user_locked.total_credits += 10
            if not user_locked.first_booking_completed:
                user_locked.first_booking_completed = True
                
            # Free Booking Counter Logic
            tier_benefits = user_locked.get_tier_benefits
            free_every = tier_benefits.get('free_booking_every', 0)
            
            if free_every > 0:
                user_locked.free_booking_counter += 1
                if user_locked.free_booking_counter >= free_every:
                    user_locked.free_booking_counter = 0
                    user_locked.free_booking_available = True
                    
            user_locked.save(update_fields=[
                'total_bookings', 'total_credits', 'first_booking_completed',
                'free_booking_counter', 'free_booking_available'
            ])

            # ── 15. Record coupon usage ──
            if applied_coupon:
                from users.models import CouponUsage
                CouponUsage.objects.get_or_create(
                    user=user,
                    coupon=applied_coupon,
                    defaults={'booking_id': booking.id},
                )
                PromoCode.objects.filter(pk=applied_coupon.pk).update(
                    current_uses=applied_coupon.current_uses + 1
                )

            logger.info(
                f"Booking #{booking.id} confirmed via service: ₹{authoritative_total} | "
                f"Turf: {turf.name} | Date: {preview.booking_date} | "
                f"Coupon: {coupon_code or 'none'}"
            )

            return {
                'success': True,
                'booking_id': booking.id,
                'booking_date': str(booking.booking_date),
                'start_time': str(booking.start_time),
                'end_time': str(booking.end_time),
                'total_payable': str(authoritative_total),
                'booking_status': booking.booking_status,
                'total_credits': user_locked.total_credits,
                'available_credits': user_locked.available_credits,
                'message': 'Booking confirmed successfully',
                'status_code': 201,
            }

    except IntegrityError:
        return {'success': False, 'error': 'Slot is no longer available', 'status_code': 409}

    except ValueError as e:
        logger.error(f"LEDGER IMBALANCE: {str(e)}")
        return {'success': False, 'error': 'Financial integrity error. Please contact support.', 'status_code': 500}

    except Exception as e:
        logger.error(f"confirm_booking error: {str(e)}", exc_info=True)
        return {'success': False, 'error': 'An unexpected error occurred', 'status_code': 500}
