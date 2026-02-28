"""
Signals for growth app — triggered on booking confirmation.
Handles: referral cashback, streak updates, first booking detection, captain mode cashback.
"""

import logging
from decimal import Decimal
from datetime import timedelta

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from bookings.models import Booking
from .models import ReferralReward, WalletTransaction, UserStreak, TeamBooking

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Booking)
def on_booking_saved(sender, instance, created, **kwargs):
    """
    Post-save hook on Booking.
    Only triggers on status change to 'confirmed'.
    """
    booking = instance

    # Only process confirmed bookings
    if booking.booking_status != 'confirmed':
        return

    user = booking.user

    # --- First booking detection ---
    confirmed_count = Booking.objects.filter(
        user=user, booking_status='confirmed'
    ).count()

    if confirmed_count == 1:
        # Mark as first booking
        if not booking.is_first_booking:
            Booking.objects.filter(id=booking.id).update(is_first_booking=True)

        # Process referral rewards if user was referred
        _process_first_booking_referral(user)

    # --- Update streak ---
    _update_streak(user)


def _process_first_booking_referral(user):
    """Award cashback when a referred user makes their first booking."""
    if not user.referred_by:
        return

    referrer = user.referred_by
    now = timezone.now()

    try:
        # 1. Pay the pending install reward (₹10 to referrer)
        install_reward = ReferralReward.objects.filter(
            referrer=referrer, referee=user, stage='install', status='pending'
        ).first()

        if install_reward:
            install_reward.status = 'paid'
            install_reward.paid_at = now
            install_reward.save()

            # Credit ₹10 to referrer wallet
            referrer.wallet_balance += Decimal('10.00')
            referrer.referral_cashback_earned += Decimal('10.00')
            referrer.save(update_fields=['wallet_balance', 'referral_cashback_earned'])

            WalletTransaction.objects.create(
                user=referrer,
                amount=Decimal('10.00'),
                type='credit',
                description=f'Referral: {user.first_name or user.username} installed',
                expires_at=now + timedelta(days=30),
            )

        # 2. Create and pay booking reward (₹40 to referrer)
        ReferralReward.objects.create(
            referrer=referrer,
            referee=user,
            stage='booking',
            amount=Decimal('40.00'),
            status='paid',
            paid_at=now,
        )

        referrer.wallet_balance += Decimal('40.00')
        referrer.referral_cashback_earned += Decimal('40.00')
        referrer.qualified_referrals += 1
        referrer.save(update_fields=[
            'wallet_balance', 'referral_cashback_earned', 'qualified_referrals'
        ])

        WalletTransaction.objects.create(
            user=referrer,
            amount=Decimal('40.00'),
            type='credit',
            description=f'Referral: {user.first_name or user.username} first booking',
            expires_at=now + timedelta(days=30),
        )

        # 3. Credit ₹30 to the friend (referee)
        user.wallet_balance += Decimal('30.00')
        user.save(update_fields=['wallet_balance'])

        WalletTransaction.objects.create(
            user=user,
            amount=Decimal('30.00'),
            type='credit',
            description='Welcome cashback — first booking!',
            expires_at=now + timedelta(days=30),
        )

        logger.info(
            f"Referral rewards processed: {referrer.username} earned ₹50, "
            f"{user.username} earned ₹30"
        )

    except Exception as e:
        logger.error(f"Error processing referral rewards: {e}", exc_info=True)


def _update_streak(user):
    """Update user's booking streak."""
    try:
        streak, _ = UserStreak.objects.get_or_create(user=user)
        today = timezone.now().date()

        if streak.last_booking_date is None:
            streak.current_streak = 1
            streak.last_booking_date = today
        elif streak.last_booking_date == today:
            # Already booked today, no change
            return
        elif (today - streak.last_booking_date).days <= 7:
            streak.current_streak += 1
            streak.last_booking_date = today
        else:
            # Streak broken — reset
            streak.current_streak = 1
            streak.last_booking_date = today

        # Update longest
        if streak.current_streak > streak.longest_streak:
            streak.longest_streak = streak.current_streak

        # Check for streak rewards
        if streak.current_streak >= streak.next_reward_at:
            reward_amount = Decimal(str(streak.current_streak * 10))
            user.wallet_balance += reward_amount
            user.save(update_fields=['wallet_balance'])

            WalletTransaction.objects.create(
                user=user,
                amount=reward_amount,
                type='credit',
                description=f'Streak reward — {streak.current_streak} week streak! 🔥',
                expires_at=timezone.now() + timedelta(days=30),
            )

            # Set next milestone
            streak.next_reward_at = streak.current_streak + 3

            logger.info(
                f"Streak reward: {user.username} earned ₹{reward_amount} "
                f"for {streak.current_streak} week streak"
            )

        streak.save()

    except Exception as e:
        logger.error(f"Error updating streak: {e}", exc_info=True)


def award_captain_cashback(team_booking):
    """Award cashback for captain mode: ₹20 to member, ₹10 to captain."""
    try:
        now = timezone.now()
        captain = team_booking.captain
        member = team_booking.member

        if team_booking.cashback_awarded:
            return

        # ₹20 to member
        if member:
            member.wallet_balance += Decimal('20.00')
            member.save(update_fields=['wallet_balance'])

            WalletTransaction.objects.create(
                user=member,
                amount=Decimal('20.00'),
                type='credit',
                description=f'Team cashback — joined {captain.first_name or captain.username}\'s game!',
                expires_at=now + timedelta(days=30),
            )

        # ₹10 to captain
        captain.wallet_balance += Decimal('10.00')
        captain.save(update_fields=['wallet_balance'])

        WalletTransaction.objects.create(
            user=captain,
            amount=Decimal('10.00'),
            type='credit',
            description=f'Captain bonus — {member.first_name or member.username} joined!',
            expires_at=now + timedelta(days=30),
        )

        team_booking.cashback_awarded = True
        team_booking.save(update_fields=['cashback_awarded'])

        logger.info(
            f"Captain cashback: captain {captain.username} +₹10, "
            f"member {member.username} +₹20"
        )

    except Exception as e:
        logger.error(f"Error awarding captain cashback: {e}", exc_info=True)
