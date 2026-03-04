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


# ---------------------------------------------------------------------------
# Offer Config — central admin-editable config for all offer types
# ---------------------------------------------------------------------------

class OfferConfig(models.Model):
    """Central configuration for all offers — editable via Truff-Admin."""

    OFFER_TYPE_CHOICES = [
        ('first_booking', 'First Booking Discount'),
        ('referral', 'Referral Program'),
        ('last_minute', 'Last Minute Deals'),
        ('streak', 'Streak Rewards'),
        ('loyalty', 'Loyalty Tiers'),
        ('captain', 'Captain Rewards'),
        ('wallet', 'Wallet Cashback'),
    ]

    name = models.CharField(max_length=100)
    offer_type = models.CharField(
        max_length=50, choices=OFFER_TYPE_CHOICES, unique=True, db_index=True
    )
    is_active = models.BooleanField(default=True)

    # Common fields
    discount_amount = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    discount_percent = models.IntegerField(null=True, blank=True)
    min_order_value = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )

    # Time-based
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)
    expiry_days = models.IntegerField(default=30)  # Wallet / first booking expiry

    # Streak — [threshold_weeks, ...] and [reward_amount, ...] (0 = free booking)
    streak_thresholds = models.JSONField(default=list, blank=True)
    streak_rewards = models.JSONField(default=list, blank=True)

    # Loyalty — [min_bookings, ...] and [perk_text, ...]
    loyalty_tiers = models.JSONField(default=list, blank=True)
    loyalty_perks = models.JSONField(default=list, blank=True)

    # Referral / Captain — {"install":10, "booking":40, "friend":50} or {"captain":10, "teammate":20}
    referral_rewards = models.JSONField(default=dict, blank=True)

    # Last-minute windows — [[from_hrs, to_hrs, discount_pct], ...]
    last_minute_windows = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='offer_configs_updated',
    )

    class Meta:
        verbose_name = 'Offer Config'
        verbose_name_plural = 'Offer Configs'
        ordering = ['offer_type']

    def __str__(self):
        status = 'ON' if self.is_active else 'OFF'
        return f"{self.name} [{self.offer_type}] ({status})"

    @classmethod
    def get_active(cls, offer_type):
        """Return the active config for a given offer type, or None."""
        try:
            obj = cls.objects.get(offer_type=offer_type)
            return obj if obj.is_active else None
        except cls.DoesNotExist:
            return None

    @classmethod
    def get_or_create_default(cls, offer_type, name):
        """Get or create a config with sensible defaults."""
        defaults = {
            'first_booking': {
                'name': 'First Booking Discount',
                'is_active': True,
                'discount_amount': 100,
                'expiry_days': 7,
            },
            'referral': {
                'name': 'Referral Program',
                'is_active': True,
                'referral_rewards': {'install': 10, 'booking': 40, 'friend': 50},
            },
            'last_minute': {
                'name': 'Last Minute Deals',
                'is_active': True,
                'last_minute_windows': [[6, 24, 10], [3, 6, 25], [0, 3, 50]],
            },
            'streak': {
                'name': 'Streak Rewards',
                'is_active': True,
                'streak_thresholds': [2, 4, 8, 12],
                'streak_rewards': [25, 75, 0, 200],
            },
            'loyalty': {
                'name': 'Loyalty Tiers',
                'is_active': True,
                'loyalty_tiers': [5, 15, 30, 50],
                'loyalty_perks': ['5% cashback', '10% cashback', '15% cashback', 'Free every 10th'],
            },
            'captain': {
                'name': 'Captain Rewards',
                'is_active': True,
                'referral_rewards': {'captain': 10, 'teammate': 20},
            },
            'wallet': {
                'name': 'Wallet Cashback',
                'is_active': True,
                'discount_percent': 5,
                'expiry_days': 30,
                'min_order_value': 50,
            },
        }
        d = defaults.get(offer_type, {'name': name})
        obj, created = cls.objects.get_or_create(offer_type=offer_type, defaults=d)
        return obj


# ---------------------------------------------------------------------------
# Offer Usage — immutable usage log for analytics
# ---------------------------------------------------------------------------

class OfferUsage(models.Model):
    """Append-only log of each offer reward / redemption."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='offer_usages',
    )
    offer_type = models.CharField(max_length=50, db_index=True)
    offer_config = models.ForeignKey(
        OfferConfig,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='usages',
    )
    booking = models.ForeignKey(
        'bookings.Booking',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='offer_usages',
    )
    reward_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['offer_type', 'created_at']),
            models.Index(fields=['user', 'offer_type']),
        ]
        verbose_name = 'Offer Usage'
        verbose_name_plural = 'Offer Usages'

    def __str__(self):
        return f"{self.offer_type} — {self.user} — ₹{self.reward_amount} [{self.created_at:%Y-%m-%d}]"
