"""
User models for TurfSpotX backend.
"""

from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone
import secrets


class CustomUser(AbstractUser):
    """Custom user model with role-based access."""
    
    class UserRole(models.TextChoices):
        USER = 'user', 'Normal User'
        TURF_OWNER = 'turf_owner', 'Turf Owner'
        PLATFORM_ADMIN = 'admin', 'Platform Admin'
    
    phone_number = models.CharField(max_length=15, blank=True, null=True)
    role = models.CharField(max_length=20, choices=UserRole.choices, default=UserRole.USER)
    profile_picture = models.ImageField(upload_to='profile_pictures/', blank=True, null=True)
    bio = models.TextField(blank=True, null=True)
    is_verified = models.BooleanField(default=True)  # Auto-verified: booking app
    is_phone_verified = models.BooleanField(default=False)

    # Referral system
    referral_code = models.CharField(max_length=10, unique=True, null=True, blank=True)
    referred_by = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='referrals'
    )
    referral_click_count = models.IntegerField(default=0)
    referral_install_count = models.IntegerField(default=0)
    referral_cashback_earned = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_referrals = models.IntegerField(default=0)
    qualified_referrals = models.IntegerField(default=0)

    # Wallet
    wallet_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    referred_by_owner = models.ForeignKey(
        'TurfOwner', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='referred_users',
    )

    # Credit system — server-side counters (never computed client-side)
    total_bookings = models.PositiveIntegerField(default=0)
    total_credits = models.PositiveIntegerField(default=0)
    used_credits = models.PositiveIntegerField(default=0)

    # First booking flag — one-way, never resets (for welcome banner)
    first_booking_completed = models.BooleanField(default=False)

    # FCM push notification token — updated each app launch
    fcm_token = models.TextField(blank=True, null=True, help_text='Firebase Cloud Messaging device token')

    # Loyalty tracking fields
    loyalty_tier = models.CharField(max_length=20, default='newbie')
    tier_updated_at = models.DateTimeField(auto_now=True)
    free_booking_counter = models.IntegerField(default=0)
    free_booking_available = models.BooleanField(default=False)
    birthday_credited_year = models.IntegerField(null=True, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def available_credits(self):
        """Derived — never stored in DB."""
        return self.total_credits - self.used_credits
    
    @property
    def get_tier_benefits(self):
        """Return tier-specific benefits"""
        tiers = {
            'newbie': {
                'cashback': 0,
                'early_booking_days': 0,
                'free_cancellation_hours': 0,
                'free_booking_every': 0,
                'dedicated_support': False,
                'birthday_bonus': 0,
            },
            'bronze': {
                'cashback': 5,
                'early_booking_days': 2,
                'free_cancellation_hours': 6,
                'free_booking_every': 0,
                'dedicated_support': False,
                'birthday_bonus': 50,
            },
            'silver': {
                'cashback': 10,
                'early_booking_days': 4,
                'free_cancellation_hours': 12,
                'free_booking_every': 0,
                'dedicated_support': 'email',
                'birthday_bonus': 100,
            },
            'gold': {
                'cashback': 15,
                'early_booking_days': 7,
                'free_cancellation_hours': 24,
                'free_booking_every': 10,
                'dedicated_support': 'phone',
                'birthday_bonus': 200,
            },
            'platinum': {
                'cashback': 20,
                'early_booking_days': 14,
                'free_cancellation_hours': 48,
                'free_booking_every': 5,
                'dedicated_support': 'manager',
                'birthday_bonus': 500,
            },
        }
        return tiers.get(self.loyalty_tier, tiers['newbie'])
    
    def update_tier(self):
        """Update tier based on total_bookings"""
        if self.total_bookings >= 50:
            new_tier = 'platinum'
        elif self.total_bookings >= 30:
            new_tier = 'gold'
        elif self.total_bookings >= 15:
            new_tier = 'silver'
        elif self.total_bookings >= 5:
            new_tier = 'bronze'
        else:
            new_tier = 'newbie'
        
        if self.loyalty_tier != new_tier:
            self.loyalty_tier = new_tier
            self.save()
            return True
        return False
    
    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"

    def save(self, *args, **kwargs):
        if not self.referral_code:
            self.referral_code = secrets.token_hex(3).upper()
        super().save(*args, **kwargs)

    class Meta:
        ordering = ['-created_at']


class UserFavorite(models.Model):
    """User's favorite turfs — server-side persistence."""
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='favorites')
    turf = models.ForeignKey('turfs.Turf', on_delete=models.CASCADE, related_name='favorited_by')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'turf')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} ♥ {self.turf.name}"


class TurfOwner(models.Model):
    """Extended profile for turf owners."""
    
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='turf_owner_profile')
    bank_account = models.CharField(max_length=50, blank=True, null=True)
    ifsc_code = models.CharField(max_length=11, blank=True, null=True)
    bank_name = models.CharField(max_length=100, blank=True, null=True)
    account_holder_name = models.CharField(max_length=100, blank=True, null=True)
    gst_number = models.CharField(max_length=15, blank=True, null=True)
    bank_verified = models.BooleanField(default=False)
    agreement_accepted_at = models.DateTimeField(null=True, blank=True)
    total_turfs = models.IntegerField(default=0)
    total_bookings = models.IntegerField(default=0)
    total_revenue = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    rating = models.FloatField(
        default=4.5,
        validators=[MinValueValidator(0.0), MaxValueValidator(5.0)]
    )
    # QR code promotion tracking
    qr_scans = models.IntegerField(default=0)
    qr_installs = models.IntegerField(default=0)
    qr_bookings = models.IntegerField(default=0)
    qr_earnings = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"TurfOwner: {self.user.username}"
    
    class Meta:
        ordering = ['-created_at']


# ---------------------------------------------------------------------------
# OTP Verification
# ---------------------------------------------------------------------------

class OTPRequest(models.Model):
    """OTP verification requests for phone number validation."""
    PURPOSE_REGISTRATION = 'registration'
    PURPOSE_RESET = 'reset'
    PURPOSE_LOGIN = 'login'
    PURPOSE_PHONE_CHANGE = 'phone_change'
    PURPOSE_CHOICES = [
        (PURPOSE_REGISTRATION, 'Registration'),
        (PURPOSE_RESET, 'Password Reset'),
        (PURPOSE_LOGIN, 'Login'),
        (PURPOSE_PHONE_CHANGE, 'Phone Change'),
    ]

    METHOD_SMS = 'sms'
    METHOD_CHOICES = [
        (METHOD_SMS, 'SMS'),
    ]

    phone = models.CharField(max_length=15)
    code = models.CharField(max_length=6)
    purpose = models.CharField(
        max_length=20, choices=PURPOSE_CHOICES, default='', blank=True
    )
    # Channel used for delivery (sms only)
    method = models.CharField(
        max_length=5, choices=METHOD_CHOICES, default=METHOD_SMS
    )
    # Number of failed verify attempts — locked out after OTP_MAX_ATTEMPTS
    attempts = models.PositiveSmallIntegerField(default=0)
    expires_at = models.DateTimeField()
    is_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def is_valid(self):
        return not self.is_verified and timezone.now() < self.expires_at

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['phone', '-created_at']),
            models.Index(fields=['phone', 'purpose', 'is_verified']),
        ]

    def __str__(self):
        return f"OTP {self.phone}/{self.purpose} ({self.code}) - {'valid' if self.is_valid() else 'expired'}"


# ---------------------------------------------------------------------------
# Referral System
# ---------------------------------------------------------------------------

class Referral(models.Model):
    """Tracks referral relationships and credit awards."""
    referrer = models.ForeignKey(CustomUser, related_name='referrals_made', on_delete=models.CASCADE)
    referee = models.ForeignKey(CustomUser, related_name='referred_by_user', on_delete=models.CASCADE)
    credits_awarded = models.IntegerField(default=0)
    booking = models.ForeignKey('bookings.Booking', null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('referrer', 'referee')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.referrer.username} → {self.referee.username} (+{self.credits_awarded} credits)"


# ---------------------------------------------------------------------------
# Promo Codes
# ---------------------------------------------------------------------------

class PromoCode(models.Model):
    """Promotional discount codes."""
    DISCOUNT_TYPE_CHOICES = [
        ('percentage', 'Percentage'),
        ('fixed', 'Fixed Amount'),
    ]

    code = models.CharField(max_length=20, unique=True)
    discount_type = models.CharField(max_length=10, choices=DISCOUNT_TYPE_CHOICES)
    discount_value = models.DecimalField(max_digits=10, decimal_places=2)
    min_order_value = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    max_discount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    valid_from = models.DateTimeField()
    valid_until = models.DateTimeField()
    max_uses = models.IntegerField(default=1)
    current_uses = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def is_valid(self):
        now = timezone.now()
        return (
            self.is_active
            and self.valid_from <= now <= self.valid_until
            and self.current_uses < self.max_uses
        )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.code} ({self.discount_type}: {self.discount_value})"


class CouponUsage(models.Model):
    """Tracks which users have used which promo codes — enforces one-time use per user."""
    user = models.ForeignKey(
        'CustomUser',
        on_delete=models.CASCADE,
        related_name='coupon_usages',
    )
    coupon = models.ForeignKey(
        PromoCode,
        on_delete=models.CASCADE,
        related_name='usages',
    )
    booking_id = models.IntegerField(null=True, blank=True)  # Avoids circular import with bookings app
    used_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'coupon')  # One use per coupon per user
        ordering = ['-used_at']

    def __str__(self):
        return f"{self.user} used {self.coupon.code} on {self.used_at:%Y-%m-%d}"


# ---------------------------------------------------------------------------
# Push Notifications
# ---------------------------------------------------------------------------

class DeviceToken(models.Model):
    """FCM device tokens for push notifications."""
    DEVICE_TYPE_CHOICES = [
        ('android', 'Android'),
        ('ios', 'iOS'),
    ]

    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='device_tokens')
    token = models.CharField(max_length=255)
    device_type = models.CharField(max_length=20, choices=DEVICE_TYPE_CHOICES)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'token')
        ordering = ['-updated_at']

    def __str__(self):
        return f"{self.user.username} ({self.device_type})"


# ---------------------------------------------------------------------------
# In-App Chat (polling-based, no WebSockets)
# ---------------------------------------------------------------------------

class ChatRoom(models.Model):
    """Chat room between a user and a turf owner about a specific turf."""
    user = models.ForeignKey(CustomUser, related_name='chat_rooms_as_user', on_delete=models.CASCADE)
    owner = models.ForeignKey(CustomUser, related_name='chat_rooms_as_owner', on_delete=models.CASCADE)
    turf = models.ForeignKey('turfs.Turf', on_delete=models.CASCADE, related_name='chat_rooms')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'owner', 'turf')
        ordering = ['-updated_at']

    def __str__(self):
        return f"Chat: {self.user.username} ↔ {self.owner.username} ({self.turf.name})"


class ChatMessage(models.Model):
    """Individual chat message."""
    room = models.ForeignKey(ChatRoom, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['room', 'created_at']),
        ]

    def __str__(self):
        return f"{self.sender.username}: {self.message[:50]}"


# ---------------------------------------------------------------------------
# Dispute Resolution
# ---------------------------------------------------------------------------

class Dispute(models.Model):
    """Dispute ticket system for user/owner issues."""
    REASON_CHOICES = [
        ('cancellation', 'Cancellation Issue'),
        ('payment', 'Payment Issue'),
        ('no_show', 'Owner No-Show'),
        ('facility', 'Facility Not as Described'),
        ('other', 'Other'),
    ]
    STATUS_CHOICES = [
        ('open', 'Open'),
        ('investigating', 'Investigating'),
        ('resolved', 'Resolved'),
        ('closed', 'Closed'),
    ]

    booking = models.ForeignKey('bookings.Booking', on_delete=models.CASCADE, related_name='disputes')
    raised_by = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='disputes_raised')
    reason = models.CharField(max_length=50, choices=REASON_CHOICES)
    description = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    admin_notes = models.TextField(blank=True, default='')
    resolution = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at']),
        ]

    def __str__(self):
        return f"Dispute #{self.pk} — {self.get_reason_display()} ({self.get_status_display()})"
