"""
Turf models for TurfZone backend.
"""

import logging
from datetime import time as dt_time

from django.db import models
from django.contrib.auth import get_user_model
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone

User = get_user_model()
logger = logging.getLogger(__name__)


class TurfStatus(models.TextChoices):
    PENDING = 'pending', 'Pending Approval'
    APPROVED = 'approved', 'Approved'
    REJECTED = 'rejected', 'Rejected'
    SUSPENDED = 'suspended', 'Suspended'


class Sport(models.Model):
    """Available sports at turfs."""
    name = models.CharField(max_length=50, unique=True)
    icon = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    
    def __str__(self):
        return self.name
    
    class Meta:
        ordering = ['name']


class Amenity(models.Model):
    """Available amenities at turfs."""
    name = models.CharField(max_length=100, unique=True)
    icon = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    
    def __str__(self):
        return self.name
    
    class Meta:
        ordering = ['name']
        verbose_name_plural = 'Amenities'


class Turf(models.Model):
    """Main Turf model."""
    
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='turfs')
    
    # Basic Information
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, null=True)
    address = models.TextField()
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=10, blank=True, null=True)
    
    # Location (from Google Maps)
    latitude = models.FloatField()
    longitude = models.FloatField()
    
    # Pricing and Details
    price_per_hour = models.IntegerField(validators=[MinValueValidator(1)])
    max_players = models.IntegerField(default=22, validators=[MinValueValidator(1)])
    
    # Status
    status = models.CharField(max_length=20, choices=TurfStatus.choices, default=TurfStatus.PENDING)
    rejection_reason = models.TextField(blank=True, null=True)
    suspend_reason = models.TextField(blank=True, null=True)
    
    # Relationships
    sports = models.ManyToManyField(Sport, related_name='turfs')
    amenities = models.ManyToManyField(Amenity, related_name='turfs')
    
    # Ratings and Reviews
    rating = models.FloatField(
        default=4.5,
        validators=[MinValueValidator(0.0), MaxValueValidator(5.0)]
    )
    review_count = models.IntegerField(default=0)
    
    # Metadata
    is_active = models.BooleanField(default=False)
    google_maps_share_link = models.URLField(blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    approved_at = models.DateTimeField(blank=True, null=True)
    approved_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='approved_turfs',
    )
    
    def __str__(self):
        return f"{self.name} ({self.city})"
    
    def auto_create_default_slots(self):
        """Create default hourly SlotMaster entries for all 7 days.
        Uses turf.price_per_hour as base_price. Skips if active slots already exist.
        Returns number of slots created."""
        if self.slot_masters.filter(is_active=True).exists():
            logger.info('Turf %d already has active slots — skipping auto-create', self.pk)
            return 0
        slots = []
        for day in range(7):  # Mon=0 … Sun=6
            for hour in range(24):
                slots.append(SlotMaster(
                    turf=self,
                    day_of_week=day,
                    start_time=dt_time(hour, 0),
                    end_time=dt_time((hour + 1) % 24, 0),
                    base_price=self.price_per_hour,
                    is_active=True,
                ))
        SlotMaster.objects.bulk_create(slots, ignore_conflicts=True)
        logger.info('Auto-created %d default slots for turf %d', len(slots), self.pk)
        return len(slots)

    def _ensure_owner_profile(self):
        """Ensure TurfOwner profile exists and role/stats are correct."""
        from users.models import TurfOwner
        # Upgrade role if needed
        if self.owner.role == 'user':
            self.owner.role = 'turf_owner'
            self.owner.is_verified = True
            self.owner.save()
        elif not self.owner.is_verified:
            self.owner.is_verified = True
            self.owner.save()
        # Ensure TurfOwner profile exists
        profile, created = TurfOwner.objects.get_or_create(user=self.owner)
        if created:
            logger.info('Auto-created TurfOwner profile for user %s', self.owner.pk)
        # Recalculate total_turfs
        profile.total_turfs = Turf.objects.filter(owner=self.owner).count()
        profile.save(update_fields=['total_turfs'])

    def approve(self, admin=None):
        """Approve this turf listing, auto-create slots, ensure owner profile."""
        self.status = TurfStatus.APPROVED
        self.is_active = True
        self.approved_at = timezone.now()
        self.approved_by = admin
        self.rejection_reason = None
        self.suspend_reason = None
        self.save()

        self._ensure_owner_profile()
        self.auto_create_default_slots()
    
    def reject(self, reason=None):
        """Reject this turf listing."""
        self.status = TurfStatus.REJECTED
        self.is_active = False
        if reason:
            self.rejection_reason = reason
        self.save()

    def suspend(self, reason=None):
        """Suspend this turf listing."""
        self.status = TurfStatus.SUSPENDED
        self.is_active = False
        if reason:
            self.suspend_reason = reason
        self.save()

    def reactivate(self, admin=None):
        """Reactivate a suspended/rejected turf, ensure slots exist."""
        self.status = TurfStatus.APPROVED
        self.is_active = True
        self.approved_at = timezone.now()
        self.approved_by = admin
        self.suspend_reason = None
        self.save()
        self.auto_create_default_slots()

    def resubmit(self):
        """Owner resubmits a rejected turf for review."""
        self.status = TurfStatus.PENDING
        self.rejection_reason = None
        self.save()
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'is_active']),
            models.Index(fields=['latitude', 'longitude']),
            models.Index(fields=['owner', 'status']),
        ]


class TurfImage(models.Model):
    """Images for turfs."""
    turf = models.ForeignKey(Turf, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='turf_images/')
    caption = models.CharField(max_length=255, blank=True, null=True)
    is_cover = models.BooleanField(default=False)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"Image: {self.turf.name}"
    
    class Meta:
        ordering = ['-is_cover', '-uploaded_at']


class Review(models.Model):
    """Reviews for turfs."""
    turf = models.ForeignKey(Turf, on_delete=models.CASCADE, related_name='reviews')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    rating = models.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    comment = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"Review: {self.turf.name} - {self.rating}★"
    
    class Meta:
        ordering = ['-created_at']
        unique_together = ['turf', 'user']  # One review per user per turf


# ---------------------------------------------------------------------------
# Slot & Offer models — per-slot precision
# ---------------------------------------------------------------------------

class DayOfWeek(models.IntegerChoices):
    MONDAY = 0, 'Monday'
    TUESDAY = 1, 'Tuesday'
    WEDNESDAY = 2, 'Wednesday'
    THURSDAY = 3, 'Thursday'
    FRIDAY = 4, 'Friday'
    SATURDAY = 5, 'Saturday'
    SUNDAY = 6, 'Sunday'


class SlotMaster(models.Model):
    """
    Defines one recurring time slot for a turf on a specific day of the week.
    Example: Turf #1, Monday, 06:00–07:00, ₹500.
    """
    turf = models.ForeignKey(Turf, on_delete=models.CASCADE, related_name='slot_masters')
    day_of_week = models.IntegerField(choices=DayOfWeek.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()
    base_price = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['turf', 'day_of_week', 'start_time']
        unique_together = ['turf', 'day_of_week', 'start_time', 'end_time']
        indexes = [
            models.Index(fields=['turf', 'day_of_week', 'is_active']),
        ]

    def __str__(self):
        return f"{self.turf.name} | {self.get_day_of_week_display()} {self.start_time}–{self.end_time} | ₹{self.base_price}"


class OfferType(models.TextChoices):
    PERCENTAGE = 'percentage', 'Percentage'
    FLAT = 'flat', 'Flat Amount'


class SlotOffer(models.Model):
    """
    Per-slot offer/discount. Tied to a specific SlotMaster.
    Supports percentage (with optional cap) and flat discounts.
    """
    slot_master = models.ForeignKey(SlotMaster, on_delete=models.CASCADE, related_name='offers')
    offer_type = models.CharField(max_length=10, choices=OfferType.choices)
    value = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text='Percentage (e.g. 20.00 for 20%) or flat amount (e.g. 100.00 for ₹100 off)',
    )
    max_discount_cap = models.DecimalField(
        max_digits=10, decimal_places=2,
        null=True, blank=True,
        help_text='Maximum discount ceiling for percentage offers. Ignored for flat offers.',
    )
    valid_from = models.DateField()
    valid_until = models.DateField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['slot_master', 'is_active', 'valid_from', 'valid_until']),
        ]

    def __str__(self):
        if self.offer_type == OfferType.PERCENTAGE:
            cap = f" (cap ₹{self.max_discount_cap})" if self.max_discount_cap else ""
            return f"{self.value}% off{cap} on {self.slot_master}"
        return f"₹{self.value} off on {self.slot_master}"

    def calculate_discount(self, base_price):
        """
        Calculate the absolute discount amount for a given base_price.
        Uses Decimal — never float.
        """
        from decimal import Decimal, ROUND_HALF_UP
        base = Decimal(str(base_price))

        if self.offer_type == OfferType.PERCENTAGE:
            discount = (base * self.value / Decimal('100')).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
            if self.max_discount_cap is not None:
                discount = min(discount, self.max_discount_cap)
        else:
            discount = min(self.value, base)  # Flat discount cannot exceed base price

        return discount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


class BlockedSlot(models.Model):
    """
    Admin can block a specific slot on a specific date.
    E.g. maintenance, private booking, weather.
    """
    turf = models.ForeignKey(Turf, on_delete=models.CASCADE, related_name='blocked_slots')
    slot_master = models.ForeignKey(SlotMaster, on_delete=models.CASCADE, related_name='blocks')
    date = models.DateField()
    reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['slot_master', 'date']
        ordering = ['date', 'slot_master__start_time']

    def __str__(self):
        return f"BLOCKED: {self.slot_master} on {self.date} — {self.reason}"
