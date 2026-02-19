"""
Views for bookings app.
Implements: availability (browsing), preview (financial), confirm (atomic).

CONCURRENCY STACK (10 lakh+ users):
1. BookingSlot table — UniqueConstraint('slot_master', 'booking_date')
2. select_for_update() — row-level PostgreSQL locks in confirm
3. transaction.atomic() — DB rollback on any failure
4. BookingPreview.is_used — idempotency flag
5. IntegrityError catch — last-resort duplicate guard
"""

import logging
import uuid
from datetime import datetime, timedelta, date, time
from decimal import Decimal, ROUND_HALF_UP

from django.contrib.auth import get_user_model
from django.db import transaction, IntegrityError
from django.db.models import Q, F, DateTimeField, ExpressionWrapper
from django.utils import timezone

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated

from .models import Booking, BookingPreview, BookingSlot, Payment, BookingStatus, PaymentStatus
from .serializers import (
    BookingListSerializer,
    BookingDetailSerializer,
    BookingCreateSerializer,
    BookingUpdateSerializer,
    BookingCancelSerializer,
    BookingConfirmSerializer,
)
from turfs.models import Turf, SlotMaster, SlotOffer, BlockedSlot, OfferType, TurfStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Financial constants — single source of truth
# ---------------------------------------------------------------------------
GST_RATE = Decimal('0.18')             # 18% GST
COMMISSION_RATE = Decimal('0.05')       # 5% platform commission
PLATFORM_FEE = Decimal('0.00')          # Platform fee per booking (configurable)
QUANTIZE_PLACES = Decimal('0.01')


def _quantize(value):
    """Round to 2 decimal places using ROUND_HALF_UP. Always Decimal in, Decimal out."""
    return Decimal(str(value)).quantize(QUANTIZE_PLACES, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Canonical slot status values — single source of truth
# ---------------------------------------------------------------------------
SLOT_STATUS_AVAILABLE = 'available'
SLOT_STATUS_BOOKED    = 'booked'
SLOT_STATUS_BLOCKED   = 'blocked'
SLOT_STATUS_PAST      = 'past'
SLOT_STATUS_DISABLED  = 'disabled'

# Map status → Flutter-friendly error code
_STATUS_ERROR_CODE = {
    SLOT_STATUS_DISABLED: 'slot_disabled',
    SLOT_STATUS_PAST:     'slot_past',
    SLOT_STATUS_BOOKED:   'slot_booked',
    SLOT_STATUS_BLOCKED:  'slot_blocked',
}


def compute_slot_status(slot, booking_date, booked_ids, blocked_ids):
    """
    SINGLE SOURCE OF TRUTH for slot state.

    Priority order (EXACTLY):
      1. past      (booking_date/time already elapsed)
      2. disabled  (slot.is_active == False)
      3. booked    (confirmed booking exists)
      4. available

    Returns dict:
      {
        'status':       str,   # "available" | "past" | "booked" | "disabled"
        'is_available': bool,
        'is_disabled':  bool,
        'is_past':      bool,
        'is_booked':    bool,
        'is_blocked':   bool,
      }
    """
    # Timezone-aware past check
    now = timezone.localtime()
    try:
        slot_start_dt = timezone.make_aware(
            datetime.combine(booking_date, slot.start_time),
            timezone.get_current_timezone(),
        )
        is_past = now >= slot_start_dt
    except Exception:
        is_past = False

    is_disabled = not slot.is_active
    is_booked = slot.id in booked_ids
    is_blocked = slot.id in blocked_ids

    # Priority: past > disabled > booked > available
    if is_past:
        slot_status = SLOT_STATUS_PAST
    elif is_disabled:
        slot_status = SLOT_STATUS_DISABLED
    elif is_booked:
        slot_status = SLOT_STATUS_BOOKED
    else:
        slot_status = SLOT_STATUS_AVAILABLE

    is_available = slot_status == SLOT_STATUS_AVAILABLE

    return {
        'status':       slot_status,
        'is_available': is_available,
        'is_disabled':  is_disabled,
        'is_past':      is_past,
        'is_booked':    is_booked,
        'is_blocked':   is_blocked,
    }


def _find_best_offer_for_slot(slot_master, booking_date):
    """
    Find the active SlotOffer that yields the maximum absolute discount
    for a given slot on a given date. Returns (SlotOffer, discount_amount) or (None, Decimal('0.00')).
    """
    today = booking_date
    offers = SlotOffer.objects.filter(
        slot_master=slot_master,
        is_active=True,
        valid_from__lte=today,
        valid_until__gte=today,
    )

    best_offer = None
    best_discount = Decimal('0.00')

    for offer in offers:
        discount = offer.calculate_discount(slot_master.base_price)
        if discount > best_discount:
            best_discount = discount
            best_offer = offer

    return best_offer, best_discount


def _compute_slot_pricing(slot_master, booking_date):
    """
    Compute pricing for a single slot. Returns dict with all pricing fields.
    NO GST here — this is the browsing/availability layer.
    """
    original_price = _quantize(slot_master.base_price)
    offer, discount = _find_best_offer_for_slot(slot_master, booking_date)
    final_price = _quantize(original_price - discount)

    return {
        'slot_id': slot_master.id,
        'start_time': slot_master.start_time.strftime('%H:%M'),
        'end_time': slot_master.end_time.strftime('%H:%M'),
        'original_price': str(original_price),
        'final_price': str(final_price),
        'discount_amount': str(discount),
        'has_offer': offer is not None,
        'offer_type': offer.offer_type if offer else None,
        'offer_value': str(offer.value) if offer else None,
    }


def _compute_financial_breakdown(slots_pricing):
    """
    Compute full financial breakdown from slot pricing list.
    GST is ONLY calculated here (preview/confirm layer), never in availability.
    Returns dict with all financial fields.
    """
    subtotal = sum(Decimal(s['final_price']) for s in slots_pricing)
    subtotal = _quantize(subtotal)

    discount_total = sum(Decimal(s['discount_amount']) for s in slots_pricing)
    discount_total = _quantize(discount_total)

    gst_amount = _quantize(subtotal * GST_RATE)
    platform_fee = _quantize(PLATFORM_FEE)
    gst_on_platform_fee = _quantize(platform_fee * GST_RATE)
    total_payable = _quantize(subtotal + gst_amount + platform_fee + gst_on_platform_fee)
    commission = _quantize(subtotal * COMMISSION_RATE)
    owner_payout = _quantize(subtotal - commission)

    return {
        'subtotal': subtotal,
        'discount_total': discount_total,
        'gst_amount': gst_amount,
        'platform_fee': platform_fee,
        'gst_on_platform_fee': gst_on_platform_fee,
        'total_payable': total_payable,
        'commission': commission,
        'owner_payout': owner_payout,
    }


def _create_ledger_entries(booking, financials):
    """
    Create 4 double-entry ledger rows for a confirmed booking.
    Enforces sum(debit) == sum(credit).
    """
    from finance.models import LedgerEntry, EntryType, LedgerAccount

    entries = [
        # DEBIT: customer pays total
        LedgerEntry(
            booking=booking,
            entry_type=EntryType.DEBIT,
            account=LedgerAccount.CUSTOMER_PAYMENT,
            amount=financials['total_payable'],
            description='Customer payment received',
        ),
        # CREDIT: slot revenue (subtotal)
        LedgerEntry(
            booking=booking,
            entry_type=EntryType.CREDIT,
            account=LedgerAccount.SLOT_REVENUE,
            amount=financials['subtotal'],
            description='Slot revenue',
        ),
        # CREDIT: GST collected on slots
        LedgerEntry(
            booking=booking,
            entry_type=EntryType.CREDIT,
            account=LedgerAccount.GST_COLLECTED,
            amount=financials['gst_amount'],
            description='GST collected (18% on slot revenue)',
        ),
        # CREDIT: platform fee revenue
        LedgerEntry(
            booking=booking,
            entry_type=EntryType.CREDIT,
            account=LedgerAccount.PLATFORM_FEE_REVENUE,
            amount=financials['platform_fee'],
            description='Platform fee',
        ),
        # CREDIT: GST on platform fee
        LedgerEntry(
            booking=booking,
            entry_type=EntryType.CREDIT,
            account=LedgerAccount.GST_ON_PLATFORM_FEE,
            amount=financials['gst_on_platform_fee'],
            description='GST on platform fee',
        ),
    ]

    # Bulk create
    LedgerEntry.objects.bulk_create(entries)

    # Verify ledger balance
    total_debit = sum(e.amount for e in entries if e.entry_type == EntryType.DEBIT)
    total_credit = sum(e.amount for e in entries if e.entry_type == EntryType.CREDIT)

    if total_debit != total_credit:
        raise ValueError(
            f"LEDGER IMBALANCE: debit={total_debit} != credit={total_credit} "
            f"for booking #{booking.id}. Transaction will be rolled back."
        )

    return entries


class BookingViewSet(viewsets.ModelViewSet):
    """
    ViewSet for booking management.
    Implements: list, create, retrieve, update, cancel, availability, preview, confirm.
    """
    permission_classes = [IsAuthenticated]  # Default — overridden below for public actions

    def get_permissions(self):
        """Availability is public (browsing). All booking operations require auth."""
        if self.action == 'availability':
            return [AllowAny()]
        return [IsAuthenticated()]

    def get_queryset(self):
        user = self.request.user
        queryset = Booking.objects.select_related('turf', 'user', 'payment')

        if user.role == 'admin':
            return queryset

        # All non-admin users (including turf_owner) see only their OWN bookings
        return queryset.filter(user=user)

    def get_serializer_class(self):
        if self.action == 'create':
            return BookingCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return BookingUpdateSerializer
        elif self.action == 'retrieve':
            return BookingDetailSerializer
        return BookingListSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            booking = serializer.save()
            return Response({
                'success': True,
                'message': 'Booking created successfully',
                'data': BookingDetailSerializer(booking).data,
            }, status=status.HTTP_201_CREATED)
        return Response({
            'success': False,
            'errors': serializer.errors,
        }, status=status.HTTP_400_BAD_REQUEST)

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()

        # Filters
        booking_status_filter = request.query_params.get('status')
        if booking_status_filter:
            queryset = queryset.filter(booking_status=booking_status_filter)

        date_filter = request.query_params.get('date')
        if date_filter:
            queryset = queryset.filter(booking_date=date_filter)

        turf_id_filter = request.query_params.get('turf_id')
        if turf_id_filter:
            queryset = queryset.filter(turf_id=turf_id_filter)

        serializer = self.get_serializer(queryset, many=True)
        return Response({
            'success': True,
            'count': queryset.count(),
            'results': serializer.data,
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'])
    def my_bookings(self, request):
        """
        Dynamic booking lifecycle classification.

        Uses make_aware(datetime.combine(booking_date, end_time))
        compared against timezone.now() for correct timezone-safe comparison.

        Rules:
          Scheduled = CONFIRMED AND end_datetime > now
          Completed = CONFIRMED AND end_datetime <= now
          Cancelled = CANCELLED
        """
        now = timezone.now()                     # timezone-aware (UTC)
        local_now = timezone.localtime(now)       # convert to Asia/Kolkata
        today = local_now.date()                  # IST date
        current_time = local_now.time()           # IST time
        tz_info = timezone.get_current_timezone()  # Asia/Kolkata

        # ━━━━━━ RAW DIAGNOSTIC: bypass get_queryset entirely ━━━━━━
        raw_by_user = Booking.objects.filter(user=request.user)
        raw_by_user_confirmed = raw_by_user.filter(booking_status=BookingStatus.CONFIRMED)
        raw_all = Booking.objects.all()

        # Also check: does request.user own any turfs? check bookings on those turfs
        raw_by_turf_owner = Booking.objects.filter(turf__owner=request.user)

        logger.info(
            f"\n━━━ RAW DIAGNOSTIC ━━━\n"
            f"  request.user.id     = {request.user.id}\n"
            f"  request.user        = {request.user}\n"
            f"  request.user.role   = {getattr(request.user, 'role', 'NO_ROLE_ATTR')}\n"
            f"  ALL bookings in DB  = {raw_all.count()}\n"
            f"  By user=req.user    = {raw_by_user.count()}\n"
            f"  By user CONFIRMED   = {raw_by_user_confirmed.count()}\n"
            f"  By turf__owner      = {raw_by_turf_owner.count()}\n"
            f"  get_queryset count  = {self.get_queryset().count()}\n"
            f"  get_qs + user filt  = {self.get_queryset().filter(user=request.user).count()}"
        )

        # Print every booking's user_id to find mismatches
        for b in raw_all.order_by('-created_at')[:10]:
            logger.info(
                f"  DB Booking #{b.id}: user_id={b.user_id}, "
                f"turf={b.turf_id}, status={b.booking_status}, "
                f"date={b.booking_date}, end={b.end_time}"
            )

        # ━━━━━━ ACTUAL CLASSIFICATION ━━━━━━
        # Use raw Booking.objects.filter(user=request.user) directly
        # instead of get_queryset() to bypass role-based filtering
        user_bookings = Booking.objects.select_related(
            'turf', 'user', 'payment',
        ).filter(user=request.user)

        # Scheduled: CONFIRMED + end still in future
        scheduled = user_bookings.filter(
            booking_status=BookingStatus.CONFIRMED,
        ).filter(
            Q(booking_date__gt=today) |
            Q(booking_date=today, end_time__gt=current_time),
        ).order_by('booking_date', 'start_time')

        # Completed: CONFIRMED + end has passed
        completed = user_bookings.filter(
            booking_status=BookingStatus.CONFIRMED,
        ).filter(
            Q(booking_date__lt=today) |
            Q(booking_date=today, end_time__lte=current_time),
        ).order_by('-booking_date', '-start_time')

        # Cancelled
        cancelled = user_bookings.filter(
            booking_status=BookingStatus.CANCELLED,
        ).order_by('-cancelled_at')

        # ━━━━━━ DEBUG: per-booking classification ━━━━━━
        debug_bookings = []
        for b in user_bookings.order_by('-created_at')[:20]:
            combined = datetime.combine(b.booking_date, b.end_time)
            combined_aware = timezone.make_aware(combined, tz_info)
            is_future = combined_aware > now

            if b.booking_status == BookingStatus.CANCELLED:
                classification = 'cancelled'
            elif b.booking_status == BookingStatus.CONFIRMED and is_future:
                classification = 'scheduled'
            elif b.booking_status == BookingStatus.CONFIRMED and not is_future:
                classification = 'completed'
            else:
                classification = f'other ({b.booking_status})'

            debug_bookings.append({
                'id': b.id,
                'user_id': b.user_id,
                'booking_status': b.booking_status,
                'booking_date': str(b.booking_date),
                'start_time': str(b.start_time),
                'end_time': str(b.end_time),
                'combined_end_datetime': str(combined_aware),
                'is_future': is_future,
                'classification': classification,
            })

        sched_count = scheduled.count()
        comp_count = completed.count()
        canc_count = cancelled.count()
        total = user_bookings.count()

        logger.info(
            f"\n━━━ CLASSIFICATION RESULT ━━━\n"
            f"  timezone.now()      = {now}\n"
            f"  localtime(now)      = {local_now}\n"
            f"  today (IST)         = {today}\n"
            f"  current_time (IST)  = {current_time}\n"
            f"  total user bookings = {total}\n"
            f"  scheduled           = {sched_count}\n"
            f"  completed           = {comp_count}\n"
            f"  cancelled           = {canc_count}"
        )
        for d in debug_bookings:
            logger.info(
                f"  Booking #{d['id']}: user_id={d['user_id']}, "
                f"status={d['booking_status']}, "
                f"date={d['booking_date']}, end={d['end_time']}, "
                f"end_dt={d['combined_end_datetime']}, "
                f"future={d['is_future']} → {d['classification']}"
            )

        return Response({
            'success': True,
            'scheduled': BookingListSerializer(scheduled, many=True).data,
            'completed': BookingListSerializer(completed, many=True).data,
            'cancelled': BookingListSerializer(cancelled, many=True).data,
            '_debug': {
                'request_user_id': request.user.id,
                'request_user_role': getattr(request.user, 'role', 'NO_ROLE_ATTR'),
                'server_now_utc': str(now),
                'server_now_local': str(local_now),
                'today_ist': str(today),
                'current_time_ist': str(current_time),
                'raw_counts': {
                    'all_bookings_in_db': raw_all.count(),
                    'by_user_eq_request_user': raw_by_user.count(),
                    'by_user_confirmed': raw_by_user_confirmed.count(),
                    'by_turf_owner': raw_by_turf_owner.count(),
                },
                'total_user_bookings': total,
                'counts': {
                    'scheduled': sched_count,
                    'completed': comp_count,
                    'cancelled': canc_count,
                },
                'all_bookings': debug_bookings,
            },
        }, status=status.HTTP_200_OK)


    # -----------------------------------------------------------------------
    # PHASE 2: Availability — browsing layer only, NO GST
    # -----------------------------------------------------------------------
    @action(detail=False, methods=['get'])
    def availability(self, request):
        """
        Get slot-level availability and pricing for a turf on a specific date.
        This is the BROWSING layer — shows prices and discounts but NO GST.

        GET /api/bookings/bookings/availability/?turf_id=1&date=2026-02-18

        Each slot returns:
          is_available (bool) — the ONLY field frontend should use for tap gating
          is_past (bool)
          is_booked (bool)
          is_blocked (bool)
          status (str) — "available" | "past" | "booked" | "blocked"
        """
        turf_id = request.query_params.get('turf_id')
        date_str = request.query_params.get('date')

        if not turf_id or not date_str:
            return Response({
                'success': False,
                'error': 'turf_id and date are required',
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            turf = Turf.objects.get(id=turf_id, status=TurfStatus.APPROVED, is_active=True)
        except Turf.DoesNotExist:
            return Response({
                'success': False,
                'error': 'Turf not found or not active',
            }, status=status.HTTP_404_NOT_FOUND)

        try:
            booking_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            return Response({
                'success': False,
                'error': 'Invalid date format. Use YYYY-MM-DD',
            }, status=status.HTTP_400_BAD_REQUEST)

        # Get day of week (Monday=0, Sunday=6)
        day_of_week = booking_date.weekday()

        # Fetch ALL slots for this day (including disabled) — never deleted slots
        slots = SlotMaster.objects.filter(
            turf=turf,
            day_of_week=day_of_week,
        ).order_by('start_time')

        if not slots.exists():
            return Response({
                'success': True,
                'turf_id': int(turf_id),
                'date': date_str,
                'day_of_week': day_of_week,
                'slots': [],
                'message': 'Slots not yet configured for this day.',
            }, status=status.HTTP_200_OK)

        # --- Booked check: use BookingSlot (database-enforced) ---
        booked_slot_ids = set(
            BookingSlot.objects.filter(
                slot_master__turf=turf,
                booking_date=booking_date,
            ).values_list('slot_master_id', flat=True)
        )

        # --- Blocked check ---
        blocked_slot_ids = set(
            BlockedSlot.objects.filter(
                turf=turf,
                date=booking_date,
            ).values_list('slot_master_id', flat=True)
        )

        result_slots = []
        for slot in slots:
            # --- State (single source of truth) ---
            state = compute_slot_status(slot, booking_date, booked_slot_ids, blocked_slot_ids)

            # --- Pricing (offer calc happens AFTER state, offers NOT applied to non-available) ---
            try:
                pricing = _compute_slot_pricing(slot, booking_date)
            except Exception as e:
                logger.error(f"Pricing error for slot {slot.id}: {e}")
                pricing = {
                    'slot_id': slot.id,
                    'start_time': slot.start_time.strftime('%H:%M'),
                    'end_time': slot.end_time.strftime('%H:%M'),
                    'original_price': str(_quantize(slot.base_price)),
                    'final_price': str(_quantize(slot.base_price)),
                    'discount_amount': '0.00',
                    'has_offer': False,
                    'offer_type': None,
                    'offer_value': None,
                }

            # Merge state into pricing
            pricing.update(state)
            result_slots.append(pricing)

        return Response({
            'success': True,
            'turf_id': int(turf_id),
            'date': date_str,
            'day_of_week': day_of_week,
            'slots': result_slots,
        }, status=status.HTTP_200_OK)

    # -----------------------------------------------------------------------
    # PHASE 3: Preview — financial layer, GST calculated here
    # -----------------------------------------------------------------------
    @action(detail=False, methods=['post'])
    def preview(self, request):
        """
        Calculate full financial breakdown for selected slots.
        Returns preview_token (5-minute expiry) for atomic confirmation.

        POST /api/bookings/bookings/preview/
        Body: {"turf_id": 1, "booking_date": "2026-02-18", "slot_ids": [42, 43]}
        """
        turf_id = request.data.get('turf_id')
        date_str = request.data.get('booking_date')
        slot_ids = request.data.get('slot_ids', [])

        if not turf_id or not date_str or not slot_ids:
            return Response({
                'success': False,
                'error': 'turf_id, booking_date, and slot_ids are required',
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            turf = Turf.objects.get(id=turf_id, status=TurfStatus.APPROVED, is_active=True)
        except Turf.DoesNotExist:
            return Response({
                'success': False,
                'error': 'Turf not found or not active',
            }, status=status.HTTP_404_NOT_FOUND)

        try:
            booking_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            return Response({
                'success': False,
                'error': 'Invalid date format. Use YYYY-MM-DD',
            }, status=status.HTTP_400_BAD_REQUEST)

        # Validate slots exist and belong to this turf (fetch ALL, check state)
        slots = SlotMaster.objects.filter(
            id__in=slot_ids,
            turf=turf,
        ).order_by('start_time')

        if slots.count() != len(slot_ids):
            return Response({
                'success': False,
                'error': 'One or more slot_ids are invalid',
            }, status=status.HTTP_400_BAD_REQUEST)

        # --- Check ALL slots via compute_slot_status ---
        booked_slot_ids = set(
            BookingSlot.objects.filter(
                slot_master__in=slot_ids,
                booking_date=booking_date,
            ).values_list('slot_master_id', flat=True)
        )
        blocked_slot_ids = set(
            BlockedSlot.objects.filter(turf=turf, date=booking_date)
            .values_list('slot_master_id', flat=True)
        )

        for slot in slots:
            state = compute_slot_status(slot, booking_date, booked_slot_ids, blocked_slot_ids)
            if state['status'] != SLOT_STATUS_AVAILABLE:
                error_code = _STATUS_ERROR_CODE.get(state['status'], 'slot_unavailable')
                return Response({
                    'success': False,
                    'error': f"Slot {slot.start_time.strftime('%H:%M')}-{slot.end_time.strftime('%H:%M')} is {state['status']}",
                    'error_code': error_code,
                }, status=status.HTTP_400_BAD_REQUEST)

        # Compute per-slot pricing
        slots_pricing = []
        for slot in slots:
            pricing = _compute_slot_pricing(slot, booking_date)
            slots_pricing.append(pricing)

        # Compute full financial breakdown (GST calculated here only)
        financials = _compute_financial_breakdown(slots_pricing)

        # Create preview token (5-minute expiry)
        preview = BookingPreview.objects.create(
            user=request.user,
            turf=turf,
            booking_date=booking_date,
            selected_slots=slots_pricing,
            subtotal=financials['subtotal'],
            discount_total=financials['discount_total'],
            gst_amount=financials['gst_amount'],
            platform_fee=financials['platform_fee'],
            gst_on_platform_fee=financials['gst_on_platform_fee'],
            total_payable=financials['total_payable'],
            commission=financials['commission'],
            owner_payout=financials['owner_payout'],
            expires_at=timezone.now() + timedelta(minutes=5),
        )

        return Response({
            'success': True,
            'preview_token': str(preview.preview_token),
            'turf_id': turf.id,
            'turf_name': turf.name,
            'booking_date': date_str,
            'slots': slots_pricing,
            'subtotal': str(financials['subtotal']),
            'discount_total': str(financials['discount_total']),
            'gst_amount': str(financials['gst_amount']),
            'platform_fee': str(financials['platform_fee']),
            'gst_on_platform_fee': str(financials['gst_on_platform_fee']),
            'total_payable': str(financials['total_payable']),
            'commission': str(financials['commission']),
            'owner_payout': str(financials['owner_payout']),
            'expires_at': preview.expires_at.isoformat(),
        }, status=status.HTTP_200_OK)

    # -----------------------------------------------------------------------
    # PHASE 3: Confirm — atomic financial transaction
    # -----------------------------------------------------------------------
    @action(detail=False, methods=['post'])
    def confirm(self, request):
        """
        Atomic booking confirmation with ledger entries.

        POST /api/bookings/bookings/confirm/
        Body: {"preview_token": "uuid-here", "total_payable": "1180.00"}

        CONCURRENCY PROTECTION STACK:
        1. transaction.atomic() — DB rollback on error
        2. select_for_update() on BookingPreview — locks preview row
        3. select_for_update() on SlotMaster — locks slot rows
        4. BookingSlot.UniqueConstraint — DB rejects duplicate
        5. IntegrityError catch — last-resort safety net
        6. is_used flag — idempotency
        7. Total re-verification — catches price drift
        """
        token_str = request.data.get('preview_token')
        client_total = request.data.get('total_payable')

        if not token_str or not client_total:
            return Response({
                'success': False,
                'error': 'preview_token and total_payable are required',
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            client_total = Decimal(str(client_total))
        except Exception:
            return Response({
                'success': False,
                'error': 'total_payable must be a valid number',
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                # 1. Fetch and lock preview token
                try:
                    preview = BookingPreview.objects.select_for_update().get(
                        preview_token=token_str,
                        user=request.user,
                    )
                except BookingPreview.DoesNotExist:
                    return Response({
                        'success': False,
                        'error': 'Invalid preview token',
                    }, status=status.HTTP_404_NOT_FOUND)

                # 2. Check idempotency — already used?
                if preview.is_used:
                    return Response({
                        'success': False,
                        'error': 'This preview token has already been used',
                    }, status=status.HTTP_409_CONFLICT)

                # 3. Check expiry
                if preview.is_expired:
                    return Response({
                        'success': False,
                        'error': 'Preview token has expired. Please create a new preview.',
                    }, status=status.HTTP_410_GONE)

                # 3b. Re-verify turf is still APPROVED (may have been suspended since preview)
                turf = preview.turf
                if turf.status != TurfStatus.APPROVED or not turf.is_active:
                    return Response({
                        'success': False,
                        'error': 'This turf is no longer available for booking',
                        'error_code': 'turf_not_available_unapproved',
                    }, status=status.HTTP_400_BAD_REQUEST)

                # 4. Lock SlotMaster rows (row-level lock)
                slot_ids = [s['slot_id'] for s in preview.selected_slots]
                slots = SlotMaster.objects.select_for_update().filter(
                    id__in=slot_ids, turf=preview.turf,
                )

                if slots.count() != len(slot_ids):
                    return Response({
                        'success': False,
                        'error': 'One or more slots are no longer valid',
                    }, status=status.HTTP_409_CONFLICT)

                # 5. Re-run compute_slot_status inside the lock (race-condition safe)
                blocked_ids = set(
                    BlockedSlot.objects.filter(
                        turf=preview.turf, date=preview.booking_date,
                    ).values_list('slot_master_id', flat=True)
                )
                existing_locks = set(
                    BookingSlot.objects.select_for_update().filter(
                        slot_master__in=slot_ids,
                        booking_date=preview.booking_date,
                    ).values_list('slot_master_id', flat=True)
                )

                for slot in slots:
                    state = compute_slot_status(
                        slot, preview.booking_date, existing_locks, blocked_ids
                    )
                    if state['status'] != SLOT_STATUS_AVAILABLE:
                        error_code = _STATUS_ERROR_CODE.get(state['status'], 'slot_unavailable')
                        return Response({
                            'success': False,
                            'error': f"Slot {slot.start_time.strftime('%H:%M')}-{slot.end_time.strftime('%H:%M')} is {state['status']}",
                            'error_code': error_code,
                        }, status=status.HTTP_400_BAD_REQUEST)

                # 6. Recalculate pricing (catch any drift)
                slots_pricing = []
                for slot in slots.order_by('start_time'):
                    pricing = _compute_slot_pricing(slot, preview.booking_date)
                    slots_pricing.append(pricing)

                financials = _compute_financial_breakdown(slots_pricing)

                # 7. Compare total — reject if mismatch
                if abs(financials['total_payable'] - client_total) > Decimal('0.01'):
                    return Response({
                        'success': False,
                        'error': 'Price has changed since preview. Please re-preview.',
                        'new_total_payable': str(financials['total_payable']),
                        'client_total': str(client_total),
                    }, status=status.HTTP_409_CONFLICT)

                # 8. Determine booking time range from slots
                all_start_times = [slot.start_time for slot in slots]
                all_end_times = [slot.end_time for slot in slots]
                start_time = min(all_start_times)
                end_time = max(all_end_times)

                # 9. Create booking
                booking = Booking.objects.create(
                    user=request.user,
                    turf=preview.turf,
                    preview=preview,
                    booking_date=preview.booking_date,
                    start_time=start_time,
                    end_time=end_time,
                    selected_slots=slots_pricing,
                    price_per_hour=Decimal('0.00'),  # Not applicable for slot-based booking
                    total_price=financials['subtotal'] + financials['discount_total'],
                    discount=financials['discount_total'],
                    final_price=financials['subtotal'],
                    gst_amount=financials['gst_amount'],
                    commission=financials['commission'],
                    platform_fee=financials['platform_fee'],
                    gst_on_platform_fee=financials['gst_on_platform_fee'],
                    owner_payout=financials['owner_payout'],
                    booking_status=BookingStatus.CONFIRMED,
                    payment_status=PaymentStatus.PAID,
                    idempotency_key=uuid.uuid4(),
                )

                # 10. Create BookingSlot locks — UniqueConstraint is the final safety net
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
                    # UniqueConstraint fired — another booking slipped through
                    logger.warning(
                        f"IntegrityError: duplicate slot lock for turf={preview.turf.id} "
                        f"date={preview.booking_date} slots={slot_ids}"
                    )
                    raise  # Re-raise to trigger atomic rollback

                # 11. Create ledger entries (enforces debit == credit)
                _create_ledger_entries(booking, financials)

                # 12. Mark preview as used
                preview.is_used = True
                preview.save()

                # 13. Credit system — idempotent increment
                # Guarded by preview.is_used (step 2) — double-confirm impossible.
                User = get_user_model()
                user_locked = User.objects.select_for_update().get(pk=request.user.pk)
                user_locked.total_bookings += 1
                user_locked.total_credits += 10  # Backend rule: 10 credits per booking
                user_locked.save(update_fields=['total_bookings', 'total_credits'])

                logger.info(
                    f"Booking #{booking.id} confirmed: ₹{financials['total_payable']} | "
                    f"Turf: {preview.turf.name} | Date: {preview.booking_date} | "
                    f"Credits: {user_locked.total_credits}"
                )

                return Response({
                    'success': True,
                    'message': 'Booking confirmed successfully',
                    'booking_id': booking.id,
                    'booking_date': str(booking.booking_date),
                    'start_time': str(booking.start_time),
                    'end_time': str(booking.end_time),
                    'total_payable': str(financials['total_payable']),
                    'booking_status': booking.booking_status,
                    'total_credits': user_locked.total_credits,
                    'available_credits': user_locked.available_credits,
                }, status=status.HTTP_201_CREATED)

        except IntegrityError:
            # UniqueConstraint on BookingSlot — slot was booked by another user
            return Response({
                'success': False,
                'error': 'Slot is no longer available',
            }, status=status.HTTP_409_CONFLICT)

        except ValueError as e:
            # Ledger imbalance — this is a critical error
            logger.error(f"LEDGER IMBALANCE: {str(e)}")
            return Response({
                'success': False,
                'error': 'Financial integrity error. Please contact support.',
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        except Exception as e:
            logger.error(f"Confirm error: {str(e)}", exc_info=True)
            return Response({
                'success': False,
                'error': 'An unexpected error occurred',
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """Cancel a booking. Deletes BookingSlot rows to free slots."""
        booking = self.get_object()

        if booking.user != request.user and request.user.role != 'admin':
            return Response({
                'success': False,
                'error': 'You can only cancel your own bookings',
            }, status=status.HTTP_403_FORBIDDEN)

        if booking.booking_status == BookingStatus.CANCELLED:
            return Response({
                'success': False,
                'error': 'Booking is already cancelled',
            }, status=status.HTTP_400_BAD_REQUEST)

        reason = request.data.get('reason', '')
        is_admin = request.user.role == 'admin'
        booking.cancel(reason=reason, cancelled_by_admin=is_admin)

        return Response({
            'success': True,
            'message': 'Booking cancelled successfully',
            'data': BookingDetailSerializer(booking).data,
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'])
    def redeem(self, request):
        """
        Redeem credits for a free booking.

        POST /api/bookings/bookings/redeem/
        Body: {"turf_id": 1, "booking_date": "2026-02-20", "slot_ids": [42, 43]}

        CONCURRENCY STACK (same as confirm):
        1. transaction.atomic() — DB rollback on error
        2. select_for_update() on User — locks credit balance
        3. select_for_update() on SlotMaster — locks slot rows
        4. BookingSlot.UniqueConstraint — DB rejects duplicate
        5. IntegrityError catch — last-resort safety net
        """
        turf_id = request.data.get('turf_id')
        date_str = request.data.get('booking_date')
        slot_ids = request.data.get('slot_ids', [])

        if not turf_id or not date_str or not slot_ids:
            return Response({
                'success': False,
                'error': 'turf_id, booking_date, and slot_ids are required',
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            booking_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            return Response({
                'success': False,
                'error': 'Invalid date format. Use YYYY-MM-DD',
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            turf = Turf.objects.get(id=turf_id, status=TurfStatus.APPROVED, is_active=True)
        except Turf.DoesNotExist:
            return Response({
                'success': False,
                'error': 'Turf not found or not active',
            }, status=status.HTTP_404_NOT_FOUND)

        CREDITS_REQUIRED = 100  # Backend rule: 100 credits = 1 free booking

        try:
            with transaction.atomic():
                # 1. Lock user row — check credit balance
                User = get_user_model()
                user = User.objects.select_for_update().get(pk=request.user.pk)

                if user.available_credits < CREDITS_REQUIRED:
                    return Response({
                        'success': False,
                        'error': f'Insufficient credits. Need {CREDITS_REQUIRED}, have {user.available_credits}',
                    }, status=status.HTTP_400_BAD_REQUEST)

                # 2. Lock SlotMaster rows
                slots = SlotMaster.objects.select_for_update().filter(
                    id__in=slot_ids, turf=turf, is_active=True,
                )

                if slots.count() != len(slot_ids):
                    return Response({
                        'success': False,
                        'error': 'One or more slots are invalid or inactive',
                    }, status=status.HTTP_400_BAD_REQUEST)

                # 3. Re-check ALL availability inside the lock
                blocked_ids = set(
                    BlockedSlot.objects.filter(
                        turf=turf, date=booking_date,
                    ).values_list('slot_master_id', flat=True)
                )

                existing_locks = set(
                    BookingSlot.objects.select_for_update().filter(
                        slot_master__in=slot_ids,
                        booking_date=booking_date,
                    ).values_list('slot_master_id', flat=True)
                )

                for slot in slots:
                    if _is_slot_past(booking_date, slot.start_time, slot.end_time):
                        return Response({
                            'success': False,
                            'error': f'Slot {slot.start_time}-{slot.end_time} is in the past',
                        }, status=status.HTTP_400_BAD_REQUEST)

                    if slot.id in blocked_ids:
                        return Response({
                            'success': False,
                            'error': f'Slot {slot.start_time}-{slot.end_time} is blocked',
                        }, status=status.HTTP_409_CONFLICT)

                    if slot.id in existing_locks:
                        return Response({
                            'success': False,
                            'error': f'Slot {slot.start_time}-{slot.end_time} is already booked',
                        }, status=status.HTTP_409_CONFLICT)

                # 4. Determine booking time range
                all_start_times = [s.start_time for s in slots]
                all_end_times = [s.end_time for s in slots]
                start_time = min(all_start_times)
                end_time = max(all_end_times)

                # 5. Create free booking
                booking = Booking.objects.create(
                    user=request.user,
                    turf=turf,
                    booking_date=booking_date,
                    start_time=start_time,
                    end_time=end_time,
                    selected_slots=[{'slot_id': s.id, 'start_time': str(s.start_time), 'end_time': str(s.end_time)} for s in slots],
                    price_per_hour=Decimal('0.00'),
                    total_price=Decimal('0.00'),
                    discount=Decimal('0.00'),
                    final_price=Decimal('0.00'),
                    gst_amount=Decimal('0.00'),
                    commission=Decimal('0.00'),
                    platform_fee=Decimal('0.00'),
                    gst_on_platform_fee=Decimal('0.00'),
                    owner_payout=Decimal('0.00'),
                    is_redeemed=True,
                    credits_used=CREDITS_REQUIRED,
                    booking_status=BookingStatus.CONFIRMED,
                    payment_status=PaymentStatus.PAID,
                    idempotency_key=uuid.uuid4(),
                )

                # 6. Create BookingSlot locks — UniqueConstraint is the safety net
                try:
                    BookingSlot.objects.bulk_create([
                        BookingSlot(
                            booking=booking,
                            slot_master_id=sid,
                            booking_date=booking_date,
                        )
                        for sid in slot_ids
                    ])
                except IntegrityError:
                    logger.warning(
                        f"IntegrityError in redeem: duplicate slot lock for turf={turf.id} "
                        f"date={booking_date} slots={slot_ids}"
                    )
                    raise  # Re-raise to trigger atomic rollback

                # 7. Deduct credits + increment bookings
                user.used_credits += CREDITS_REQUIRED
                user.total_bookings += 1
                user.save(update_fields=['used_credits', 'total_bookings'])

                logger.info(
                    f"Redeem booking #{booking.id}: Turf={turf.name} | "
                    f"Date={booking_date} | Credits used={CREDITS_REQUIRED} | "
                    f"Remaining={user.available_credits}"
                )

                return Response({
                    'success': True,
                    'message': 'Free booking redeemed successfully!',
                    'booking_id': booking.id,
                    'booking_date': str(booking.booking_date),
                    'start_time': str(booking.start_time),
                    'end_time': str(booking.end_time),
                    'credits_used': CREDITS_REQUIRED,
                    'available_credits': user.available_credits,
                    'total_credits': user.total_credits,
                }, status=status.HTTP_201_CREATED)

        except IntegrityError:
            return Response({
                'success': False,
                'error': 'Slot is no longer available',
            }, status=status.HTTP_409_CONFLICT)

        except Exception as e:
            logger.error(f"Redeem error: {str(e)}", exc_info=True)
            return Response({
                'success': False,
                'error': 'An unexpected error occurred',
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
