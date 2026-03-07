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
from rest_framework.decorators import action, api_view, permission_classes
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

# ---------------------------------------------------------------------------
# Temp-token helpers (HMAC-signed, no extra dependencies)
# ---------------------------------------------------------------------------

import hmac as _hmac
import hashlib as _hashlib
import time as _time
import base64 as _base64
import json as _json_mod


def _generate_temp_token(phone: str, purpose: str, ttl: int = 300) -> str:
    """Return a base64-encoded HMAC-signed token encoding phone, purpose, expiry."""
    secret = settings.SECRET_KEY.encode()
    expires_at = int(_time.time()) + ttl
    payload = _json_mod.dumps({'phone': phone, 'purpose': purpose, 'exp': expires_at})
    sig = _hmac.new(secret, payload.encode(), _hashlib.sha256).hexdigest()
    combined = _json_mod.dumps({'payload': payload, 'sig': sig})
    return _base64.urlsafe_b64encode(combined.encode()).decode()


def _verify_temp_token(token: str):
    """Decode and verify a temp token. Returns payload dict or None if invalid/expired."""
    try:
        secret = settings.SECRET_KEY.encode()
        combined = _json_mod.loads(_base64.urlsafe_b64decode(token.encode()).decode())
        payload_str = combined['payload']
        expected_sig = _hmac.new(secret, payload_str.encode(), _hashlib.sha256).hexdigest()
        if not _hmac.compare_digest(expected_sig, combined['sig']):
            return None
        payload = _json_mod.loads(payload_str)
        if int(_time.time()) > payload['exp']:
            return None
        return payload
    except Exception:
        return None


# ---------------------------------------------------------------------------
# OTP ViewSet
# ---------------------------------------------------------------------------

class OTPViewSet(viewsets.ViewSet):
    """OTP send, verify, complete-registration, and reset-password endpoints."""

    permission_classes = [AllowAny]

    @action(detail=False, methods=['post'])
    def send_otp(self, request):
        """
        Send OTP to a phone number.

        POST /api/users/otp/send_otp/
        Body: {"phone": "9876543210", "purpose": "registration"|"reset"}
        """
        phone = request.data.get('phone', '').strip()
        purpose = request.data.get('purpose', '').strip()

        if not phone or len(phone) < 10:
            return Response({'error': 'Valid phone number required'}, status=status.HTTP_400_BAD_REQUEST)

        if purpose not in ('registration', 'reset', ''):
            return Response({'error': 'Invalid purpose'}, status=status.HTTP_400_BAD_REQUEST)

        # For password reset: verify account exists BEFORE sending OTP
        # (fail fast so the user doesn't waste time completing OTP verification)
        if purpose == 'reset':
            from django.contrib.auth import get_user_model
            User = get_user_model()
            user_exists = (
                User.objects.filter(username=phone).exists()
                or User.objects.filter(phone_number=phone).exists()
                or User.objects.filter(email__startswith=phone + '@').exists()
            )
            if not user_exists:
                return Response(
                    {'error': 'No account registered with this phone number.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

        # Rate limit: max 5 OTPs per (phone, purpose) per hour
        recent_count = OTPRequest.objects.filter(
            phone=phone,
            purpose=purpose,
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
            purpose=purpose,
            expires_at=expires_at,
        )

        otp_mode = getattr(settings, 'OTP_MODE', 'demo')

        if otp_mode == 'demo':
            logger.info(f"[DEMO OTP] Phone: {phone}, Purpose: {purpose}, Code: {code}")
            return Response({
                'success': True,
                'message': 'OTP sent (demo mode). Use 123456 for testing.',
                'demo_code': code,
            })

        # Production — integrate SMS provider here
        # send_sms(phone, f"Your TurfZone OTP is {code}")
        return Response({'success': True, 'message': 'OTP sent to your phone.'})

    @action(detail=False, methods=['post'])
    def verify_otp(self, request):
        """
        Verify OTP code. Returns a short-lived temp token on success.

        POST /api/users/otp/verify_otp/
        Body: {"phone": "9876543210", "code": "123456", "purpose": "registration"|"reset"}
        """
        phone = request.data.get('phone', '').strip()
        code = request.data.get('code', '').strip()
        purpose = request.data.get('purpose', '').strip()

        if not phone or not code:
            return Response({'error': 'Phone and code are required'}, status=status.HTTP_400_BAD_REQUEST)

        otp_mode = getattr(settings, 'OTP_MODE', 'demo')

        # Demo mode — accept 123456 as universal test code
        if otp_mode == 'demo' and code == '123456':
            temp_token = _generate_temp_token(phone, purpose)
            return Response({
                'success': True,
                'verified': True,
                'token': temp_token,
                'message': 'OTP verified (demo mode).',
            })

        # Production: verify against stored OTP
        otp = OTPRequest.objects.filter(
            phone=phone,
            code=code,
            purpose=purpose,
            is_verified=False,
            expires_at__gt=timezone.now(),
        ).order_by('-created_at').first()

        if otp:
            otp.is_verified = True
            otp.save(update_fields=['is_verified'])

            temp_token = _generate_temp_token(phone, purpose)
            return Response({'success': True, 'verified': True, 'token': temp_token})

        return Response(
            {'success': False, 'error': 'Invalid or expired OTP'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @action(detail=False, methods=['post'])
    def complete_registration(self, request):
        """
        Complete user registration after OTP verification.

        POST /api/users/otp/complete_registration/
        Body: {"name":"John","phone":"9876543210","password":"pass123","otp_token":"<token>"}
        """
        from .serializers import CustomUserDetailSerializer
        from rest_framework_simplejwt.tokens import RefreshToken
        from django.contrib.auth import get_user_model
        User = get_user_model()

        name = request.data.get('name', '').strip()
        phone = request.data.get('phone', '').strip()
        password = request.data.get('password', '').strip()
        otp_token = request.data.get('otp_token', '').strip()

        if not all([name, phone, password, otp_token]):
            return Response({'error': 'name, phone, password and otp_token are required'},
                            status=status.HTTP_400_BAD_REQUEST)

        # Validate temp token
        payload = _verify_temp_token(otp_token)
        if not payload or payload.get('phone') != phone or payload.get('purpose') != 'registration':
            return Response(
                {'error': 'Invalid or expired OTP session. Please restart verification.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Idempotent: existing user + correct password → silent login
        existing = (User.objects.filter(username=phone).first()
                    or User.objects.filter(phone_number=phone).first())
        if existing:
            if existing.check_password(password):
                refresh = RefreshToken.for_user(existing)
                return Response({
                    'success': True,
                    'message': 'Welcome back! Logged in successfully.',
                    'user': CustomUserDetailSerializer(existing).data,
                    'tokens': {'refresh': str(refresh), 'access': str(refresh.access_token)},
                })
            return Response({'error': 'Phone already registered. Please log in.'},
                            status=status.HTTP_400_BAD_REQUEST)

        # Create user
        parts = name.split()
        first_name = parts[0][:150]
        last_name = ' '.join(parts[1:])[:150] if len(parts) > 1 else ''
        user = User.objects.create_user(
            username=phone,
            phone_number=phone,
            first_name=first_name,
            last_name=last_name,
            email=f'{phone}@turfzone.app',
            password=password,
            is_verified=True,
            is_phone_verified=True,
        )

        refresh = RefreshToken.for_user(user)
        return Response({
            'success': True,
            'message': 'Account created successfully.',
            'user': CustomUserDetailSerializer(user).data,
            'tokens': {'refresh': str(refresh), 'access': str(refresh.access_token)},
        }, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'])
    def reset_password(self, request):
        """
        Reset user password after OTP verification.

        POST /api/users/otp/reset_password/
        Body: {"phone":"9876543210","new_password":"newpass123","otp_token":"<token>"}
        """
        from django.contrib.auth import get_user_model
        User = get_user_model()

        phone = request.data.get('phone', '').strip()
        new_password = request.data.get('new_password', '').strip()
        otp_token = request.data.get('otp_token', '').strip()

        if not all([phone, new_password, otp_token]):
            return Response({'error': 'phone, new_password and otp_token are required'},
                            status=status.HTTP_400_BAD_REQUEST)

        if len(new_password) < 6:
            return Response({'error': 'Password must be at least 6 characters'},
                            status=status.HTTP_400_BAD_REQUEST)

        # Validate temp token
        payload = _verify_temp_token(otp_token)
        if not payload or payload.get('phone') != phone or payload.get('purpose') != 'reset':
            return Response(
                {'error': 'Invalid or expired OTP session. Please restart verification.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Find user
        user = (User.objects.filter(username=phone).first()
                or User.objects.filter(phone_number=phone).first())
        if not user:
            return Response({'error': 'No account found for this phone number.'},
                            status=status.HTTP_404_NOT_FOUND)

        user.set_password(new_password)
        user.save(update_fields=['password'])

        return Response({'success': True, 'message': 'Password reset successfully. Please log in.'})




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


# ---------------------------------------------------------------------------
# Coupon Validation — /api/coupons/validate/
# Flutter-compatible endpoint (uses `amount` param, returns `discount` key)
# ---------------------------------------------------------------------------

from rest_framework.decorators import api_view, permission_classes
from decimal import Decimal, InvalidOperation


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def validate_coupon(request):
    """
    Validate a promo/coupon code for the Flutter payment summary screen.

    POST /api/coupons/validate/
    Body: {"code": "WEEKEND20", "amount": "999.00", "turf_id": 1}

    Response (valid):
        {"valid": true, "discount": "199.80", "message": "Coupon applied!",
         "code": "WEEKEND20", "discount_type": "percentage", "discount_value": "20.00"}

    Response (invalid):
        {"valid": false, "message": "Promo code has expired"}
    """
    code = request.data.get('code', '').strip().upper()
    turf_id = request.data.get('turf_id')

    # Accept both `amount` (Flutter) and `order_value` (legacy)
    raw_amount = request.data.get('amount') or request.data.get('order_value', '0')
    try:
        amount = Decimal(str(raw_amount)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError):
        amount = Decimal('0')

    if not code:
        return Response({'valid': False, 'message': 'Please enter a coupon code'},
                        status=status.HTTP_400_BAD_REQUEST)

    # ── Lookup ──
    try:
        promo = PromoCode.objects.get(code=code)
    except PromoCode.DoesNotExist:
        return Response({'valid': False, 'message': 'Invalid coupon code'},
                        status=status.HTTP_200_OK)  # 200 so Flutter reads the message

    # ── Validity checks — return clear human-readable messages ──
    now = timezone.now()
    if not promo.is_active:
        return Response({'valid': False, 'message': 'This coupon is inactive'},
                        status=status.HTTP_200_OK)
    if now < promo.valid_from:
        return Response({'valid': False, 'message': 'This coupon is not active yet'},
                        status=status.HTTP_200_OK)
    if now > promo.valid_until:
        return Response({'valid': False, 'message': 'This coupon has expired'},
                        status=status.HTTP_200_OK)
    if promo.current_uses >= promo.max_uses:
        return Response({'valid': False, 'message': 'This coupon has reached its usage limit'},
                        status=status.HTTP_200_OK)
    if amount < promo.min_order_value:
        return Response({
            'valid': False,
            'message': f'Minimum order value of \u20b9{promo.min_order_value:.0f} required',
        }, status=status.HTTP_200_OK)


    # -- Per-user usage check (one coupon per user) --
    from .models import CouponUsage
    if CouponUsage.objects.filter(user=request.user, coupon=promo).exists():
        return Response(
            {'valid': False, 'message': 'You have already used this coupon'},
            status=status.HTTP_200_OK,
        )

    # ── Optional turf restriction ──
    if turf_id:
        applicable = promo.applicable_turfs.all() if hasattr(promo, 'applicable_turfs') else []
        if applicable and not promo.applicable_turfs.filter(id=turf_id).exists():
            return Response({'valid': False, 'message': 'Coupon not valid for this turf'},
                            status=status.HTTP_200_OK)

    # ── Calculate discount ──
    if promo.discount_type == 'percentage':
        discount = (amount * promo.discount_value / Decimal('100')).quantize(Decimal('0.01'))
        if promo.max_discount:
            discount = min(discount, promo.max_discount)
    else:
        discount = min(promo.discount_value, amount)

    discount = discount.quantize(Decimal('0.01'))

    return Response({
        'valid': True,
        'discount': str(discount),          # key Flutter's _applyCoupon reads
        'discount_amount': str(discount),   # for backward compat
        'message': 'Coupon applied successfully!',
        'code': promo.code,
        'discount_type': promo.discount_type,
        'discount_value': str(promo.discount_value),
        'min_order_value': str(promo.min_order_value),
        'final_amount': str((amount - discount).quantize(Decimal('0.01'))),
    })


# ---------------------------------------------------------------------------
# Available Offers — /api/coupons/available/
# Returns only coupons the current user has NOT yet used
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_available_offers(request):
    """
    GET /api/coupons/available/?amount=999.00

    Returns active coupons applicable to the requesting user:
      - Not expired
      - Global usage limit not exceeded
      - Not already used by THIS user (CouponUsage check)
      - Optionally filtered: amount >= min_order_value (when ?amount= provided)
    """
    from .models import CouponUsage
    now = timezone.now()
    user = request.user

    # Optional amount filter
    raw_amount = request.query_params.get('amount', None)
    try:
        order_amount = Decimal(str(raw_amount)).quantize(Decimal('0.01')) if raw_amount else None
    except (InvalidOperation, TypeError):
        order_amount = None

    # 1. All active, date-valid coupons that still have global uses left
    qs = PromoCode.objects.filter(
        is_active=True,
        valid_from__lte=now,
        valid_until__gte=now,
    ).exclude(
        # Exclude coupons at global usage cap
        current_uses__gte=models.F('max_uses'),
    )

    # 2. Exclude coupons this user already used
    used_ids = CouponUsage.objects.filter(user=user).values_list('coupon_id', flat=True)
    qs = qs.exclude(id__in=used_ids)

    # 3. Build response
    offers = []
    for coupon in qs.order_by('valid_until'):
        # Skip if amount is known and below min_order
        if order_amount is not None and order_amount < coupon.min_order_value:
            continue

        # Human-readable saving string (e.g. "₹50 off" or "20% off")
        if coupon.discount_type == 'percentage':
            saving = f"{coupon.discount_value:.0f}% off"
            if coupon.max_discount:
                saving += f" (up to ₹{coupon.max_discount:.0f})"
        else:
            saving = f"₹{coupon.discount_value:.0f} off"

        # Description fallback
        desc = (
            f"Min order ₹{coupon.min_order_value:.0f}"
            if coupon.min_order_value > 0
            else "No minimum order"
        )

        offers.append({
            'code': coupon.code,
            'saving': saving,
            'desc': desc,
            'discount_type': coupon.discount_type,
            'discount_value': str(coupon.discount_value),
            'min_order_value': str(coupon.min_order_value),
            'valid_until': coupon.valid_until.isoformat(),
        })

    return Response({'success': True, 'offers': offers})


# ---------------------------------------------------------------------------
# FCM Token Registration — POST /api/coupons/fcm-token/
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def register_fcm_token(request):
    """
    POST /api/users/fcm-token/
    Body: { "fcm_token": "<device_token>" }

    Saves (or updates) the FCM registration token for the current user.
    Called by Flutter on every app launch after Firebase.initializeApp().
    """
    token = request.data.get('fcm_token', '').strip()
    if not token:
        return Response({'success': False, 'error': 'fcm_token is required'}, status=400)

    request.user.fcm_token = token
    request.user.save(update_fields=['fcm_token'])

    return Response({'success': True, 'message': 'FCM token registered.'})




# ---------------------------------------------------------------------------
# Coupon function-based views  (imported by users/coupon_urls.py)
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def validate_coupon(request):
    """
    Validate a promo code and return discount details.

    POST /api/coupons/validate/
    Body: {"code": "SAVE20", "amount": "500.00"}
    """
    from decimal import Decimal, InvalidOperation
    from .models import CouponUsage

    code = request.data.get('code', '').strip().upper()
    try:
        amount = Decimal(str(request.data.get('amount', '0')))
    except (InvalidOperation, TypeError):
        amount = Decimal('0')

    if not code:
        return Response({'valid': False, 'message': 'Coupon code is required'}, status=400)

    try:
        promo = PromoCode.objects.get(code=code, is_active=True)
    except PromoCode.DoesNotExist:
        return Response({'valid': False, 'message': 'Invalid coupon code'})

    now = timezone.now()
    if promo.valid_from > now or promo.valid_until < now:
        return Response({'valid': False, 'message': 'Coupon has expired'})

    if promo.current_uses >= promo.max_uses:
        return Response({'valid': False, 'message': 'Coupon usage limit reached'})

    already_used = CouponUsage.objects.filter(user=request.user, coupon=promo).exists()
    if already_used:
        return Response({'valid': False, 'message': 'You have already used this coupon'})

    if amount < promo.min_order_value:
        return Response({
            'valid': False,
            'message': f'Minimum booking value of Rs.{promo.min_order_value} required',
        })

    from decimal import Decimal as D
    if promo.discount_type == 'percentage':
        discount = amount * promo.discount_value / D('100')
        if promo.max_discount:
            discount = min(discount, promo.max_discount)
    else:
        discount = promo.discount_value

    discount = discount.quantize(D('0.01'))

    return Response({
        'valid': True,
        'code': promo.code,
        'discount': str(discount),
        'discount_type': promo.discount_type,
        'discount_value': str(promo.discount_value),
        'message': f'Coupon applied! You save Rs.{discount}',
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_available_offers(request):
    """
    Return all available offers for the current user, split into:
      admin_coupons  - global PromoCode offers set by Truff-Admin
      owner_offers   - turf-specific offers (empty until SlotOffer model added)
      offers         - legacy flat list for backward compatibility

    GET /api/coupons/available/?amount=999&turf_id=3
    """
    from decimal import Decimal, InvalidOperation
    from django.db import models as dj_models
    from .models import CouponUsage

    try:
        amount = Decimal(str(request.query_params.get('amount', '0')))
    except (InvalidOperation, TypeError):
        amount = Decimal('0')

    now = timezone.now()

    used_ids = CouponUsage.objects.filter(
        user=request.user
    ).values_list('coupon_id', flat=True)

    admin_qs = PromoCode.objects.filter(
        is_active=True,
        valid_from__lte=now,
        valid_until__gte=now,
        current_uses__lt=dj_models.F('max_uses'),
    ).exclude(id__in=used_ids).order_by('-valid_until')

    admin_coupons = []
    for p in admin_qs:
        if p.discount_type == 'percentage':
            saving = f'{p.discount_value:.0f}% OFF'
            if p.max_discount:
                saving += f' (up to Rs.{p.max_discount:.0f})'
            desc = f'{p.discount_value:.0f}% off on your booking'
        else:
            saving = f'Rs.{p.discount_value:.0f} OFF'
            desc = f'Flat Rs.{p.discount_value:.0f} off'

        if p.min_order_value > 0:
            desc += f' (min Rs.{p.min_order_value:.0f})'

        admin_coupons.append({
            'code': p.code,
            'description': desc,
            'discount_type': p.discount_type,
            'discount_value': str(p.discount_value),
            'min_order_value': str(p.min_order_value),
            'max_discount': str(p.max_discount) if p.max_discount else None,
            'valid_until': p.valid_until.isoformat(),
            'saving': saving,
            'eligible': amount >= p.min_order_value,
        })

    # Owner offers: placeholder until SlotOffer model is introduced
    owner_offers = []

    # Legacy flat list so existing Flutter _availableOffers keeps working
    offers_flat = [
        {'code': c['code'], 'saving': c['saving'], 'desc': c['description']}
        for c in admin_coupons
    ]

    return Response({
        'success': True,
        'admin_coupons': admin_coupons,
        'owner_offers': owner_offers,
        'offers': offers_flat,
    })
