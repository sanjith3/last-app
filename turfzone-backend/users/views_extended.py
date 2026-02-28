"""
Additional API views for OTP verification, chat, referrals, promo codes,
device tokens, and dispute resolution.

All endpoints are additive — no existing views are modified.
"""

import random
import string
import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response

from .models import (
    CustomUser, OTPRequest, Referral, PromoCode,
    DeviceToken, ChatRoom, ChatMessage, Dispute, TurfOwner,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OTP ViewSet
# ---------------------------------------------------------------------------

class OTPViewSet(viewsets.ViewSet):
    """OTP send and verify endpoints."""

    permission_classes = [AllowAny]

    @action(detail=False, methods=['post'])
    def send_otp(self, request):
        """
        Send OTP to a phone number.

        POST /api/users/otp/send_otp/
        Body: {"phone": "9876543210"}
        """
        phone = request.data.get('phone', '').strip()

        if not phone or len(phone) < 10:
            return Response({'error': 'Valid phone number required'}, status=status.HTTP_400_BAD_REQUEST)

        # Rate limit: max 5 OTPs per phone per hour
        recent_count = OTPRequest.objects.filter(
            phone=phone,
            created_at__gte=timezone.now() - timedelta(hours=1),
        ).count()

        if recent_count >= 5:
            return Response(
                {'error': 'Too many OTP requests. Please try again later.'},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        # Generate 6-digit OTP
        code = ''.join(random.choices(string.digits, k=6))
        expires_at = timezone.now() + timedelta(minutes=5)

        OTPRequest.objects.create(
            phone=phone,
            code=code,
            expires_at=expires_at,
        )

        otp_mode = getattr(settings, 'OTP_MODE', 'demo')

        if otp_mode == 'demo':
            logger.info(f"[DEMO OTP] Phone: {phone}, Code: {code}")
            return Response({
                'success': True,
                'message': 'OTP sent (demo mode). Use 123456 for testing.',
                'demo_code': code,
            })

        # Production mode — integrate with SMS provider here
        # sms_api_key = getattr(settings, 'SMS_API_KEY', '')
        # send_sms(phone, f"Your TurfZone OTP is {code}")

        return Response({'success': True, 'message': 'OTP sent to your phone.'})

    @action(detail=False, methods=['post'])
    def verify_otp(self, request):
        """
        Verify OTP code.

        POST /api/users/otp/verify_otp/
        Body: {"phone": "9876543210", "code": "123456"}
        """
        phone = request.data.get('phone', '').strip()
        code = request.data.get('code', '').strip()

        if not phone or not code:
            return Response({'error': 'Phone and code are required'}, status=status.HTTP_400_BAD_REQUEST)

        otp_mode = getattr(settings, 'OTP_MODE', 'demo')

        # Demo mode — accept 123456
        if otp_mode == 'demo' and code == '123456':
            if request.user and request.user.is_authenticated:
                request.user.is_phone_verified = True
                request.user.save(update_fields=['is_phone_verified'])
            return Response({'success': True, 'verified': True, 'message': 'Phone verified (demo mode).'})

        # Production mode — verify against stored OTP
        otp = OTPRequest.objects.filter(
            phone=phone,
            code=code,
            is_verified=False,
            expires_at__gt=timezone.now(),
        ).order_by('-created_at').first()

        if otp:
            otp.is_verified = True
            otp.save(update_fields=['is_verified'])

            if request.user and request.user.is_authenticated:
                request.user.is_phone_verified = True
                request.user.save(update_fields=['is_phone_verified'])

            return Response({'success': True, 'verified': True})

        return Response(
            {'success': False, 'error': 'Invalid or expired OTP'},
            status=status.HTTP_400_BAD_REQUEST,
        )


# ---------------------------------------------------------------------------
# Chat ViewSet
# ---------------------------------------------------------------------------

class ChatViewSet(viewsets.ViewSet):
    """Simple polling-based chat endpoints."""

    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['post'])
    def get_or_create_room(self, request):
        """
        Get or create a chat room.

        POST /api/users/chat/get_or_create_room/
        Body: {"turf_id": 1}
        """
        from turfs.models import Turf

        turf_id = request.data.get('turf_id')
        if not turf_id:
            return Response({'error': 'turf_id required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            turf = Turf.objects.get(id=turf_id)
        except Turf.DoesNotExist:
            return Response({'error': 'Turf not found'}, status=status.HTTP_404_NOT_FOUND)

        owner_user = turf.owner.user

        room, created = ChatRoom.objects.get_or_create(
            user=request.user,
            owner=owner_user,
            turf=turf,
        )

        return Response({
            'room_id': room.id,
            'turf_name': turf.name,
            'owner_name': owner_user.first_name or owner_user.username,
            'created': created,
        })

    @action(detail=False, methods=['get'])
    def messages(self, request):
        """
        Get messages for a chat room (polling).

        GET /api/users/chat/messages/?room_id=1&after=2026-02-20T10:00:00Z
        """
        room_id = request.query_params.get('room_id')
        after = request.query_params.get('after')

        if not room_id:
            return Response({'error': 'room_id required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            room = ChatRoom.objects.get(id=room_id)
        except ChatRoom.DoesNotExist:
            return Response({'error': 'Room not found'}, status=status.HTTP_404_NOT_FOUND)

        # Only allow room participants
        if request.user not in [room.user, room.owner]:
            return Response({'error': 'Unauthorized'}, status=status.HTTP_403_FORBIDDEN)

        qs = ChatMessage.objects.filter(room=room)
        if after:
            qs = qs.filter(created_at__gt=after)

        messages = [{
            'id': m.id,
            'text': m.message,
            'sender_id': m.sender_id,
            'sender_name': m.sender.first_name or m.sender.username,
            'is_read': m.is_read,
            'time': m.created_at.isoformat(),
        } for m in qs[:50]]

        return Response({'messages': messages, 'room_id': int(room_id)})

    @action(detail=False, methods=['post'])
    def send_message(self, request):
        """
        Send a chat message.

        POST /api/users/chat/send_message/
        Body: {"room_id": 1, "message": "Hello!"}
        """
        room_id = request.data.get('room_id')
        message_text = request.data.get('message', '').strip()

        if not room_id or not message_text:
            return Response({'error': 'room_id and message required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            room = ChatRoom.objects.get(id=room_id)
        except ChatRoom.DoesNotExist:
            return Response({'error': 'Room not found'}, status=status.HTTP_404_NOT_FOUND)

        if request.user not in [room.user, room.owner]:
            return Response({'error': 'Unauthorized'}, status=status.HTTP_403_FORBIDDEN)

        msg = ChatMessage.objects.create(
            room=room,
            sender=request.user,
            message=message_text,
        )

        # Update room timestamp
        room.save()  # triggers auto_now on updated_at

        return Response({
            'id': msg.id,
            'text': msg.message,
            'sender_id': msg.sender_id,
            'time': msg.created_at.isoformat(),
        })

    @action(detail=False, methods=['get'])
    def rooms(self, request):
        """
        List all chat rooms for the current user.

        GET /api/users/chat/rooms/
        """
        rooms = ChatRoom.objects.filter(
            user=request.user
        ) | ChatRoom.objects.filter(owner=request.user)

        # Deduplicate and order
        all_rooms = rooms.distinct().order_by('-updated_at')

        result = []
        for room in all_rooms[:20]:
            last_msg = room.messages.order_by('-created_at').first()
            unread = room.messages.filter(is_read=False).exclude(sender=request.user).count()
            result.append({
                'room_id': room.id,
                'turf_name': room.turf.name,
                'other_user': (
                    room.owner.first_name or room.owner.username
                ) if request.user == room.user else (
                    room.user.first_name or room.user.username
                ),
                'last_message': last_msg.message[:100] if last_msg else '',
                'last_message_time': last_msg.created_at.isoformat() if last_msg else None,
                'unread_count': unread,
            })

        return Response({'rooms': result})


# ---------------------------------------------------------------------------
# Promo Code ViewSet
# ---------------------------------------------------------------------------

class PromoCodeViewSet(viewsets.ViewSet):
    """Promo code validation endpoint."""

    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['post'])
    def validate(self, request):
        """
        Validate a promo code and return discount details.

        POST /api/users/promo/validate/
        Body: {"code": "SAVE20", "order_value": "500.00"}
        """
        from decimal import Decimal

        code = request.data.get('code', '').strip().upper()
        order_value = Decimal(str(request.data.get('order_value', '0')))

        if not code:
            return Response({'error': 'Promo code required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            promo = PromoCode.objects.get(code=code)
        except PromoCode.DoesNotExist:
            return Response({'valid': False, 'error': 'Invalid promo code'}, status=status.HTTP_404_NOT_FOUND)

        if not promo.is_valid():
            return Response({'valid': False, 'error': 'Promo code has expired or reached usage limit'})

        if order_value < promo.min_order_value:
            return Response({
                'valid': False,
                'error': f'Minimum order value of ₹{promo.min_order_value} required',
            })

        # Calculate discount
        if promo.discount_type == 'percentage':
            discount = order_value * promo.discount_value / Decimal('100')
            if promo.max_discount:
                discount = min(discount, promo.max_discount)
        else:
            discount = promo.discount_value

        return Response({
            'valid': True,
            'code': promo.code,
            'discount_type': promo.discount_type,
            'discount_value': str(promo.discount_value),
            'discount_amount': str(discount.quantize(Decimal('0.01'))),
            'final_amount': str((order_value - discount).quantize(Decimal('0.01'))),
        })


# ---------------------------------------------------------------------------
# Device Token ViewSet (FCM Push Notifications)
# ---------------------------------------------------------------------------

class DeviceTokenViewSet(viewsets.ViewSet):
    """Register/unregister FCM device tokens."""

    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['post'])
    def register_device(self, request):
        """
        Register a device token for push notifications.

        POST /api/users/devices/register_device/
        Body: {"token": "fcm_token_here", "device_type": "android"}
        """
        token = request.data.get('token', '').strip()
        device_type = request.data.get('device_type', 'android')

        if not token:
            return Response({'error': 'Token required'}, status=status.HTTP_400_BAD_REQUEST)

        DeviceToken.objects.update_or_create(
            user=request.user,
            token=token,
            defaults={'device_type': device_type, 'is_active': True},
        )

        return Response({'success': True, 'message': 'Device registered for notifications.'})

    @action(detail=False, methods=['post'])
    def unregister_device(self, request):
        """
        Unregister a device token.

        POST /api/users/devices/unregister_device/
        Body: {"token": "fcm_token_here"}
        """
        token = request.data.get('token', '').strip()

        if token:
            DeviceToken.objects.filter(user=request.user, token=token).update(is_active=False)

        return Response({'success': True})


# ---------------------------------------------------------------------------
# Dispute ViewSet
# ---------------------------------------------------------------------------

class DisputeViewSet(viewsets.ViewSet):
    """Create and list disputes."""

    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['post'])
    def create_dispute(self, request):
        """
        Create a dispute for a booking.

        POST /api/users/disputes/create_dispute/
        Body: {"booking_id": 123, "reason": "payment", "description": "Payment charged but booking failed"}
        """
        from bookings.models import Booking

        booking_id = request.data.get('booking_id')
        reason = request.data.get('reason', 'other')
        description = request.data.get('description', '').strip()

        if not booking_id:
            return Response({'error': 'booking_id required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            booking = Booking.objects.get(id=booking_id)
        except Booking.DoesNotExist:
            return Response({'error': 'Booking not found'}, status=status.HTTP_404_NOT_FOUND)

        # Only the booking user or the turf owner can raise a dispute
        is_user = booking.user == request.user
        is_owner = hasattr(request.user, 'turf_owner_profile') and booking.turf.owner == request.user.turf_owner_profile

        if not is_user and not is_owner:
            return Response({'error': 'Unauthorized'}, status=status.HTTP_403_FORBIDDEN)

        dispute = Dispute.objects.create(
            booking=booking,
            raised_by=request.user,
            reason=reason,
            description=description,
        )

        return Response({
            'success': True,
            'dispute_id': dispute.id,
            'status': dispute.status,
            'message': 'Dispute created. Our team will review it within 24 hours.',
        })

    @action(detail=False, methods=['get'])
    def my_disputes(self, request):
        """
        List disputes raised by the current user.

        GET /api/users/disputes/my_disputes/
        """
        disputes = Dispute.objects.filter(raised_by=request.user)

        result = [{
            'id': d.id,
            'booking_id': d.booking_id,
            'reason': d.get_reason_display(),
            'description': d.description,
            'status': d.get_status_display(),
            'resolution': d.resolution,
            'created_at': d.created_at.isoformat(),
            'updated_at': d.updated_at.isoformat(),
        } for d in disputes[:20]]

        return Response({'disputes': result})


# ---------------------------------------------------------------------------
# Referral ViewSet
# ---------------------------------------------------------------------------

class ReferralViewSet(viewsets.ViewSet):
    """Referral code management."""

    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['get'])
    def my_code(self, request):
        """
        Get the current user's referral code.

        GET /api/users/referrals/my_code/
        """
        user = request.user
        return Response({
            'referral_code': user.referral_code,
            'referrals_count': Referral.objects.filter(referrer=user).count(),
            'credits_earned': sum(
                r.credits_awarded
                for r in Referral.objects.filter(referrer=user)
            ),
        })

    @action(detail=False, methods=['post'])
    def apply_code(self, request):
        """
        Apply a referral code during registration.

        POST /api/users/referrals/apply_code/
        Body: {"referral_code": "ABC123"}
        """
        code = request.data.get('referral_code', '').strip().upper()

        if not code:
            return Response({'error': 'Referral code required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            referrer = CustomUser.objects.get(referral_code=code)
        except CustomUser.DoesNotExist:
            return Response({'error': 'Invalid referral code'}, status=status.HTTP_404_NOT_FOUND)

        if referrer == request.user:
            return Response({'error': 'You cannot use your own referral code'}, status=status.HTTP_400_BAD_REQUEST)

        # Check if already referred
        if request.user.referred_by is not None:
            return Response({'error': 'You have already used a referral code'}, status=status.HTTP_400_BAD_REQUEST)

        # Apply referral
        request.user.referred_by = referrer
        request.user.save(update_fields=['referred_by'])

        Referral.objects.get_or_create(
            referrer=referrer,
            referee=request.user,
            defaults={'credits_awarded': 0},  # Credits awarded on first booking
        )

        return Response({
            'success': True,
            'message': f'Referral code applied! You were referred by {referrer.first_name or referrer.username}.',
        })


# ---------------------------------------------------------------------------
# Bank Details update for owners
# ---------------------------------------------------------------------------

class OwnerBankDetailsViewSet(viewsets.ViewSet):
    """Update bank details for turf owners."""

    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['post'])
    def update_bank(self, request):
        """
        Update bank details for the current turf owner.

        POST /api/users/owner-bank/update_bank/
        Body: {"account_number": "...", "ifsc_code": "...", "account_holder": "...", "bank_name": "..."}
        """
        try:
            owner = request.user.turf_owner_profile
        except TurfOwner.DoesNotExist:
            return Response({'error': 'You are not a turf owner'}, status=status.HTTP_403_FORBIDDEN)

        owner.bank_account = request.data.get('account_number', owner.bank_account)
        owner.ifsc_code = request.data.get('ifsc_code', owner.ifsc_code)
        owner.account_holder_name = request.data.get('account_holder', owner.account_holder_name)
        owner.bank_name = request.data.get('bank_name', owner.bank_name)
        owner.save()

        return Response({
            'success': True,
            'message': 'Bank details updated successfully.',
            'bank_account': owner.bank_account,
            'ifsc_code': owner.ifsc_code,
            'account_holder_name': owner.account_holder_name,
            'bank_name': owner.bank_name,
        })

    @action(detail=False, methods=['get'])
    def get_bank(self, request):
        """
        Get bank details for the current turf owner.

        GET /api/users/owner-bank/get_bank/
        """
        try:
            owner = request.user.turf_owner_profile
        except TurfOwner.DoesNotExist:
            return Response({'error': 'You are not a turf owner'}, status=status.HTTP_403_FORBIDDEN)

        return Response({
            'bank_account': owner.bank_account,
            'ifsc_code': owner.ifsc_code,
            'account_holder_name': owner.account_holder_name,
            'bank_name': owner.bank_name,
            'bank_verified': owner.bank_verified,
        })
