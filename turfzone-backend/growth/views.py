"""
Views for growth app — referral, wallet, streaks, loyalty, live stats, captain mode, owner QR.
"""

import secrets
import logging
from decimal import Decimal
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Sum, Q, Count
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response

from bookings.models import Booking
from users.models import TurfOwner
from .models import ReferralReward, WalletTransaction, UserStreak, TeamBooking
from .serializers import (
    ReferralRewardSerializer,
    WalletTransactionSerializer,
    UserStreakSerializer,
    TeamBookingSerializer,
)

User = get_user_model()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Referral ViewSet
# ---------------------------------------------------------------------------

class ReferralViewSet(viewsets.ViewSet):
    """
    Referral link generation, validation, click tracking, and friends list.
    """

    def get_permissions(self):
        if self.action in ('validate', 'track_click'):
            return [AllowAny()]
        return [IsAuthenticated()]

    @action(detail=False, methods=['post'], url_path='generate-link')
    def generate_link(self, request):
        """
        Generate referral invite link for the current user.
        POST /api/growth/referral/generate-link/
        """
        user = request.user
        if not user.referral_code:
            user.referral_code = secrets.token_hex(3).upper()
            user.save(update_fields=['referral_code'])

        link = f"https://turfzone.app/join?ref={user.referral_code}"
        return Response({
            'success': True,
            'link': link,
            'code': user.referral_code,
        })

    @action(detail=False, methods=['get'], url_path='validate')
    def validate(self, request):
        """
        Validate a referral code.
        GET /api/growth/referral/validate/?code=ABC123
        """
        code = request.query_params.get('code', '').strip()
        if not code:
            return Response({'valid': False, 'error': 'Code required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            referrer = User.objects.get(referral_code=code)
            return Response({
                'valid': True,
                'referrer_name': referrer.first_name or referrer.username,
            })
        except User.DoesNotExist:
            return Response({'valid': False})

    @action(detail=False, methods=['post'], url_path='track-click')
    def track_click(self, request):
        """
        Track a click on a referral link.
        POST /api/growth/referral/track-click/
        Body: {"code": "ABC123"}
        """
        code = request.data.get('code', '').strip()
        if not code:
            return Response({'success': False}, status=status.HTTP_400_BAD_REQUEST)

        try:
            referrer = User.objects.get(referral_code=code)
            referrer.referral_click_count += 1
            referrer.save(update_fields=['referral_click_count'])
            return Response({'success': True})
        except User.DoesNotExist:
            return Response({'success': False}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=False, methods=['post'], url_path='register')
    def register_referral(self, request):
        """
        Record that a user installed via referral. Called during registration.
        POST /api/growth/referral/register/
        Body: {"code": "ABC123", "user_id": 123}
        """
        code = request.data.get('code', '').strip()
        user_id = request.data.get('user_id')

        if not code or not user_id:
            return Response({'success': False, 'error': 'code and user_id required'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            referrer = User.objects.get(referral_code=code)
            referee = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({'success': False, 'error': 'Invalid code or user'},
                            status=status.HTTP_404_NOT_FOUND)

        if referrer.id == referee.id:
            return Response({'success': False, 'error': 'Cannot refer yourself'},
                            status=status.HTTP_400_BAD_REQUEST)

        # Check if already referred
        if referee.referred_by is not None:
            return Response({'success': False, 'error': 'User already has a referrer'},
                            status=status.HTTP_409_CONFLICT)

        # Set referred_by relationship
        referee.referred_by = referrer
        referee.save(update_fields=['referred_by'])

        # Update referrer stats
        referrer.referral_install_count += 1
        referrer.total_referrals += 1
        referrer.save(update_fields=['referral_install_count', 'total_referrals'])

        # Create pending install reward (₹10)
        ReferralReward.objects.create(
            referrer=referrer,
            referee=referee,
            stage='install',
            amount=Decimal('10.00'),
            status='pending',
        )

        return Response({'success': True, 'message': 'Referral recorded'})

    @action(detail=False, methods=['get'], url_path='friends')
    def friends(self, request):
        """
        List friends referred by the current user with their status.
        GET /api/growth/referral/friends/
        """
        user = request.user
        referrals = User.objects.filter(referred_by=user).order_by('-created_at')

        friends = []
        for ref in referrals:
            has_booked = Booking.objects.filter(
                user=ref, booking_status='confirmed'
            ).exists()

            # Calculate cashback earned from this friend
            cashback = ReferralReward.objects.filter(
                referrer=user, referee=ref, status='paid'
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

            friends.append({
                'id': ref.id,
                'name': ref.first_name or ref.username,
                'status': 'booked' if has_booked else 'installed',
                'joined_at': ref.created_at,
                'cashback_earned': cashback,
            })

        return Response({
            'success': True,
            'friends': friends,
            'total': len(friends),
        })

    @action(detail=False, methods=['get'], url_path='stats')
    def stats(self, request):
        """
        Get referral stats for current user.
        GET /api/growth/referral/stats/
        """
        user = request.user
        # Ensure referral_code is never None
        if not user.referral_code:
            user.referral_code = secrets.token_hex(3).upper()
            user.save(update_fields=['referral_code'])

        return Response({
            'success': True,
            'referral_code': user.referral_code,
            'link': f"https://turfzone.app/join?ref={user.referral_code}",
            'clicks': user.referral_click_count,
            'installs': user.referral_install_count,
            'qualified': user.qualified_referrals,
            'cashback_earned': str(user.referral_cashback_earned),
        })


# ---------------------------------------------------------------------------
# Wallet ViewSet
# ---------------------------------------------------------------------------

class WalletViewSet(viewsets.ViewSet):
    """Wallet balance and transaction history."""
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['get'], url_path='balance')
    def balance(self, request):
        """
        GET /api/growth/wallet/balance/
        """
        user = request.user
        now = timezone.now()

        # Calculate pending (unpaid rewards)
        pending = ReferralReward.objects.filter(
            referrer=user, status='pending'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

        # Calculate expiring soon (within 3 days)
        expiring = WalletTransaction.objects.filter(
            user=user, type='credit', is_expired=False,
            expires_at__isnull=False,
            expires_at__lte=now + timedelta(days=3),
            expires_at__gt=now,
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

        # Total earned
        total_earned = WalletTransaction.objects.filter(
            user=user, type='credit'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

        return Response({
            'success': True,
            'balance': str(user.wallet_balance),
            'pending': str(pending),
            'total_earned': str(total_earned),
            'expiring_soon': str(expiring),
        })

    @action(detail=False, methods=['get'], url_path='transactions')
    def transactions(self, request):
        """
        GET /api/growth/wallet/transactions/
        """
        txns = WalletTransaction.objects.filter(user=request.user)[:50]
        serializer = WalletTransactionSerializer(txns, many=True)
        return Response({
            'success': True,
            'transactions': serializer.data,
        })


# ---------------------------------------------------------------------------
# Streak & Loyalty ViewSet
# ---------------------------------------------------------------------------

class StreakLoyaltyViewSet(viewsets.ViewSet):
    """Streak tracking and loyalty level endpoints."""
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['get'], url_path='streak')
    def streak(self, request):
        """
        GET /api/growth/streak-loyalty/streak/
        """
        streak, _ = UserStreak.objects.get_or_create(user=request.user)
        serializer = UserStreakSerializer(streak)
        return Response({
            'success': True,
            **serializer.data,
        })

    @action(detail=False, methods=['get'], url_path='loyalty')
    def loyalty(self, request):
        """
        GET /api/growth/streak-loyalty/loyalty/
        """
        user = request.user
        bookings = Booking.objects.filter(
            user=user, booking_status='confirmed'
        ).count()

        if bookings >= 30:
            level_data = {
                'level': 'Gold', 'emoji': '🥇',
                'next': None, 'progress': 100, 'needed': 0,
            }
        elif bookings >= 15:
            level_data = {
                'level': 'Silver', 'emoji': '🥈',
                'next': 'Gold',
                'progress': round(((bookings - 15) / 15) * 100),
                'needed': 30 - bookings,
            }
        elif bookings >= 5:
            level_data = {
                'level': 'Bronze', 'emoji': '🥉',
                'next': 'Silver',
                'progress': round(((bookings - 5) / 10) * 100),
                'needed': 15 - bookings,
            }
        else:
            level_data = {
                'level': 'Newbie', 'emoji': '🌱',
                'next': 'Bronze',
                'progress': round((bookings / 5) * 100) if bookings > 0 else 0,
                'needed': 5 - bookings,
            }

        level_data['total_bookings'] = bookings
        return Response({'success': True, **level_data})


# ---------------------------------------------------------------------------
# Live Stats ViewSet
# ---------------------------------------------------------------------------

class LiveStatsViewSet(viewsets.ViewSet):
    """Public live stats for social proof."""
    permission_classes = [AllowAny]

    @action(detail=False, methods=['get'], url_path='stats')
    def stats(self, request):
        """
        GET /api/growth/live-stats/stats/
        """
        today = timezone.now().date()

        today_bookings = Booking.objects.filter(
            booking_date=today, booking_status='confirmed'
        ).count()

        # City breakdown
        city_bookings = (
            Booking.objects.filter(booking_date=today, booking_status='confirmed')
            .values('turf__city')
            .annotate(count=Count('id'))
            .order_by('-count')[:10]
        )

        city_data = {}
        for item in city_bookings:
            city = item['turf__city'] or 'Unknown'
            city_data[city] = item['count']

        return Response({
            'success': True,
            'today_bookings': today_bookings,
            'city_bookings': city_data,
        })


# ---------------------------------------------------------------------------
# Captain Mode ViewSet
# ---------------------------------------------------------------------------

class CaptainViewSet(viewsets.ViewSet):
    """Captain Mode: team invites after booking."""
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['post'], url_path='generate-invite')
    def generate_invite(self, request):
        """
        Generate team invite link for a booking.
        POST /api/growth/captain/generate-invite/
        Body: {"booking_id": 123}
        """
        booking_id = request.data.get('booking_id')
        if not booking_id:
            return Response({'success': False, 'error': 'booking_id required'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            booking = Booking.objects.get(id=booking_id, user=request.user)
        except Booking.DoesNotExist:
            return Response({'success': False, 'error': 'Booking not found'},
                            status=status.HTTP_404_NOT_FOUND)

        # Generate unique invite code
        invite_code = secrets.token_hex(4).upper()
        link = f"https://turfzone.app/join-booking?booking={booking_id}&code={invite_code}"

        # Create invite record (no member yet)
        TeamBooking.objects.create(
            booking=booking,
            captain=request.user,
            invite_code=invite_code,
            status='invited',
        )

        return Response({
            'success': True,
            'invite_code': invite_code,
            'link': link,
            'booking_id': booking_id,
        })

    @action(detail=False, methods=['post'], url_path='join')
    def join_booking(self, request):
        """
        Join a team booking via invite code.
        POST /api/growth/captain/join/
        Body: {"invite_code": "ABC12345"}
        """
        invite_code = request.data.get('invite_code', '').strip()
        if not invite_code:
            return Response({'success': False, 'error': 'invite_code required'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            team = TeamBooking.objects.get(invite_code=invite_code, status='invited')
        except TeamBooking.DoesNotExist:
            return Response({'success': False, 'error': 'Invalid or already used invite'},
                            status=status.HTTP_404_NOT_FOUND)

        user = request.user

        if user.id == team.captain_id:
            return Response({'success': False, 'error': 'Cannot join your own booking'},
                            status=status.HTTP_400_BAD_REQUEST)

        # Mark as joined
        team.member = user
        team.status = 'joined'
        team.joined_at = timezone.now()
        team.save()

        # Award cashback: ₹20 to member, ₹10 to captain
        from .signals import award_captain_cashback
        award_captain_cashback(team)

        return Response({
            'success': True,
            'message': f'Joined team! You earned ₹20 cashback.',
            'booking_id': team.booking_id,
        })

    @action(detail=False, methods=['get'], url_path='members')
    def list_members(self, request):
        """
        Get team members for a booking.
        GET /api/growth/captain/members/?booking_id=123
        """
        booking_id = request.query_params.get('booking_id')
        if not booking_id:
            return Response({'success': False, 'error': 'booking_id required'},
                            status=status.HTTP_400_BAD_REQUEST)

        members = TeamBooking.objects.filter(
            booking_id=booking_id, captain=request.user
        )
        serializer = TeamBookingSerializer(members, many=True)
        return Response({
            'success': True,
            'members': serializer.data,
        })


# ---------------------------------------------------------------------------
# Owner QR ViewSet
# ---------------------------------------------------------------------------

class OwnerQRViewSet(viewsets.ViewSet):
    """QR code generation and stats for turf owners."""
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['post'], url_path='generate')
    def generate_qr(self, request):
        """
        Generate QR code data for turf owner.
        POST /api/growth/owner-qr/generate/
        Returns the QR data URL (the frontend generates the actual QR image).
        """
        user = request.user
        try:
            owner = TurfOwner.objects.get(user=user)
        except TurfOwner.DoesNotExist:
            return Response({'success': False, 'error': 'Not a turf owner'},
                            status=status.HTTP_403_FORBIDDEN)

        if not user.referral_code:
            user.referral_code = secrets.token_hex(3).upper()
            user.save(update_fields=['referral_code'])

        qr_data = f"https://turfzone.app/join?owner={owner.id}&code={user.referral_code}"

        return Response({
            'success': True,
            'qr_data': qr_data,
            'referral_code': user.referral_code,
            'owner_id': owner.id,
        })

    @action(detail=False, methods=['get'], url_path='stats')
    def qr_stats(self, request):
        """
        GET /api/growth/owner-qr/stats/
        """
        try:
            owner = TurfOwner.objects.get(user=request.user)
        except TurfOwner.DoesNotExist:
            return Response({'success': False, 'error': 'Not a turf owner'},
                            status=status.HTTP_403_FORBIDDEN)

        return Response({
            'success': True,
            'qr_scans': owner.qr_scans,
            'qr_installs': owner.qr_installs,
            'qr_bookings': owner.qr_bookings,
            'qr_earnings': str(owner.qr_earnings),
        })
