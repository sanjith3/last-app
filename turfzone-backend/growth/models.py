"""
Growth models — Referral rewards, wallet, streaks, loyalty, and captain mode.
All additive — no changes to existing models.
"""

from django.db import models
from django.conf import settings
from django.utils import timezone


# ---------------------------------------------------------------------------
# Referral Reward — staged cashback tracking
# ---------------------------------------------------------------------------

class ReferralReward(models.Model):
    """Tracks staged referral rewards: install (₹10 pending) + first booking (₹40 + ₹30)."""
    STAGE_CHOICES = [
        ('install', 'Install'),
        ('booking', 'First Booking'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('paid', 'Paid'),
        ('expired', 'Expired'),
    ]

    referrer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='referral_rewards_given',
        on_delete=models.CASCADE,
    )
    referee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='referral_rewards_received',
        on_delete=models.CASCADE,
    )
    stage = models.CharField(max_length=20, choices=STAGE_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['referrer', 'status']),
            models.Index(fields=['referee', 'stage']),
        ]

    def __str__(self):
        return f"Reward: {self.referrer} → {self.referee} ({self.stage}: ₹{self.amount} [{self.status}])"


# ---------------------------------------------------------------------------
# Wallet Transaction — credits/debits with optional expiry
# ---------------------------------------------------------------------------

class WalletTransaction(models.Model):
    """Individual wallet credit/debit with optional expiry date."""
    TYPE_CHOICES = [
        ('credit', 'Credit'),
        ('debit', 'Debit'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='wallet_transactions',
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    description = models.CharField(max_length=255)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_expired = models.BooleanField(default=False)
    notified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'type']),
            models.Index(fields=['expires_at']),
        ]

    def __str__(self):
        return f"{self.type.upper()} ₹{self.amount} — {self.description} ({self.user})"


# ---------------------------------------------------------------------------
# User Streak — weekly booking streak tracking
# ---------------------------------------------------------------------------

class UserStreak(models.Model):
    """Tracks weekly booking streaks and milestone rewards."""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='streak',
    )
    current_streak = models.IntegerField(default=0)
    longest_streak = models.IntegerField(default=0)
    last_booking_date = models.DateField(null=True, blank=True)
    next_reward_at = models.IntegerField(default=3)  # 3, 6, 12 weeks
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'User Streaks'

    def __str__(self):
        return f"Streak: {self.user} — {self.current_streak} weeks (longest: {self.longest_streak})"


# ---------------------------------------------------------------------------
# Team Booking (Captain Mode)
# ---------------------------------------------------------------------------

class TeamBooking(models.Model):
    """Captain Mode: tracks team members who joined a booking via invite link."""
    STATUS_CHOICES = [
        ('invited', 'Invited'),
        ('joined', 'Joined'),
    ]

    booking = models.ForeignKey(
        'bookings.Booking',
        on_delete=models.CASCADE,
        related_name='team_members',
    )
    captain = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='captain_bookings',
        on_delete=models.CASCADE,
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='team_memberships',
        on_delete=models.CASCADE,
        null=True, blank=True,
    )
    invite_code = models.CharField(max_length=20, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='invited')
    cashback_awarded = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    joined_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['booking', 'status']),
            models.Index(fields=['invite_code']),
        ]

    def __str__(self):
        member_name = self.member.username if self.member else 'pending'
        return f"Team: {self.captain} → {member_name} ({self.status})"
