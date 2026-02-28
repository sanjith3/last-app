"""
Booking models for TurfZone backend.
"""

import uuid
from decimal import Decimal
from django.db import models
from django.conf import settings
from django.utils import timezone


class BookingStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    CONFIRMED = 'confirmed', 'Confirmed'
    COMPLETED = 'completed', 'Completed'
    CANCELLED = 'cancelled', 'Cancelled'


class PaymentStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    PAID = 'paid', 'Paid'
    FAILED = 'failed', 'Failed'
    REFUNDED = 'refunded', 'Refunded'


class PayoutStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    PAID = 'paid', 'Paid'


class BookingPreview(models.Model):
    """
    Short-lived pricing snapshot created before booking confirmation.
    Token expires in 5 minutes. Used for atomic confirm flow.
    Invariant: is_used == True means this token has already been consumed.
    """
    preview_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='booking_previews',
    )
    turf = models.ForeignKey(
        'turfs.Turf',
        on_delete=models.CASCADE,
        related_name='booking_previews',
    )
    booking_date = models.DateField()

    # Snapshot of selected slots with full pricing detail
    # Format: [{"slot_id": 42, "start_time": "06:00", "end_time": "07:00",
    #           "original_price": "500.00", "discount": "100.00", "final_price": "400.00"}]
    selected_slots = models.JSONField()

    # Financial breakdown — all Decimal, no float
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    discount_total = models.DecimalField(max_digits=10, decimal_places=2)
    gst_amount = models.DecimalField(max_digits=10, decimal_places=2)
    platform_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    gst_on_platform_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    total_payable = models.DecimalField(max_digits=10, decimal_places=2)
    commission = models.DecimalField(max_digits=10, decimal_places=2)
    gst_on_commission = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    commission_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('5.00'))
    owner_payout = models.DecimalField(max_digits=10, decimal_places=2)
    platform_revenue = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    # First booking discount (₹50 for users with 0 previous bookings)
    first_booking_discount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    # Idempotency & expiry
    is_used = models.BooleanField(default=False)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['preview_token']),
            models.Index(fields=['user', 'is_used']),
            models.Index(fields=['expires_at']),
        ]

    def __str__(self):
        return f"Preview {self.preview_token} | {self.turf} | {self.booking_date} | ₹{self.total_payable}"

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at


class Booking(models.Model):
    """Turf booking model with full financial fields."""

    # Relationships
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='bookings',
    )
    turf = models.ForeignKey(
        'turfs.Turf',
        on_delete=models.CASCADE,
        related_name='bookings',
    )
    preview = models.ForeignKey(
        BookingPreview,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='booking',
    )

    # Booking Details
    booking_date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    selected_slots = models.JSONField(null=True, blank=True)

    # Pricing (kept for backward compat + quick access)
    price_per_hour = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    final_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    # Financial fields (populated by confirm flow)
    gst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    commission = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    gst_on_commission = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    commission_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('5.00'))
    platform_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    gst_on_platform_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    platform_revenue = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    owner_payout = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    payout_status = models.CharField(
        max_length=20,
        choices=PayoutStatus.choices,
        default=PayoutStatus.PENDING,
    )

    # Credit redemption — tracks free bookings
    is_redeemed = models.BooleanField(default=False)
    credits_used = models.PositiveIntegerField(default=0)
    is_first_booking = models.BooleanField(default=False)

    # Idempotency key — prevents duplicate bookings from retries
    idempotency_key = models.UUIDField(unique=True, null=True, blank=True)

    # Status
    booking_status = models.CharField(
        max_length=20,
        choices=BookingStatus.choices,
        default=BookingStatus.PENDING,
    )
    payment_status = models.CharField(
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING,
    )

    # Razorpay Payment Details
    razorpay_order_id = models.CharField(max_length=100, null=True, blank=True, db_index=True)
    razorpay_payment_id = models.CharField(max_length=100, null=True, blank=True)
    razorpay_signature = models.CharField(max_length=200, null=True, blank=True)

    # Extra Info
    notes = models.TextField(blank=True, null=True)
    cancelled_reason = models.TextField(blank=True, null=True)
    cancelled_at = models.DateTimeField(blank=True, null=True)
    cancelled_by_admin = models.BooleanField(default=False)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Booking: {self.turf.name} - {self.booking_date} ({self.booking_status})"

    def cancel(self, reason='', cancelled_by_admin=False):
        """Cancel this booking and release slot locks."""
        self.booking_status = BookingStatus.CANCELLED
        self.cancelled_reason = reason
        self.cancelled_at = timezone.now()
        self.cancelled_by_admin = cancelled_by_admin
        self.save()
        # Release slot locks so these slots become bookable again
        self.slot_locks.all().delete()

    def confirm(self):
        """Confirm this booking."""
        self.booking_status = BookingStatus.CONFIRMED
        self.payment_status = PaymentStatus.PAID
        self.save()

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'booking_date']),
            models.Index(fields=['turf', 'booking_date']),
            models.Index(fields=['booking_status']),
            models.Index(fields=['idempotency_key']),
        ]


class Payment(models.Model):
    """Payment tracking for bookings."""

    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name='payment')

    # Payment Details — all Decimal
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=50, default='card')
    transaction_id = models.CharField(max_length=100, unique=True)

    # Status
    status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"Payment: {self.transaction_id} - {self.status}"

    class Meta:
        ordering = ['-created_at']


class BookingSlot(models.Model):
    """
    Database-level slot lock.
    One row per (slot_master, booking_date) guarantees NO double booking —
    even if code-level checks fail, PostgreSQL rejects the duplicate.

    Created inside transaction.atomic() during confirm.
    Deleted when booking is cancelled (freeing the slot).
    """
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='slot_locks')
    slot_master = models.ForeignKey(
        'turfs.SlotMaster', on_delete=models.CASCADE, related_name='booked_slots',
    )
    booking_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['slot_master', 'booking_date'],
                name='unique_slot_per_date',
            ),
        ]
        indexes = [
            models.Index(fields=['slot_master', 'booking_date']),
        ]

    def __str__(self):
        return f"Lock: {self.slot_master} on {self.booking_date} → Booking #{self.booking_id}"


# ---------------------------------------------------------------------------
# Call Records — privacy-protected calling system
# ---------------------------------------------------------------------------

class CallStatus(models.TextChoices):
    INITIATED = 'initiated', 'Initiated'
    CONNECTED = 'connected', 'Connected'
    COMPLETED = 'completed', 'Completed'
    FAILED = 'failed', 'Failed'


class CallRecord(models.Model):
    """
    Tracks calls between turf owners and customers.
    Owner never sees real customer phone — all calls go through admin system.
    """
    booking = models.ForeignKey(
        Booking,
        on_delete=models.CASCADE,
        related_name='call_records',
    )
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='initiated_calls',
        help_text='The turf owner who clicked Call Customer',
    )

    # Conference / telephony
    conference_id = models.CharField(
        max_length=100, unique=True, null=True, blank=True,
        help_text='Provider conference ID (Twilio/Exotel/etc.)',
    )
    owner_called = models.BooleanField(default=False)
    customer_called = models.BooleanField(default=False)

    # Timing
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.IntegerField(default=0)

    # Status
    status = models.CharField(
        max_length=20,
        choices=CallStatus.choices,
        default=CallStatus.INITIATED,
    )

    # Optional recording
    recording_url = models.URLField(null=True, blank=True)

    # Admin notification tracking
    admin_notified = models.BooleanField(default=False)
    admin_acknowledged = models.BooleanField(default=False)
    admin_acknowledged_at = models.DateTimeField(null=True, blank=True)
    admin_notes = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['booking', '-started_at']),
            models.Index(fields=['initiated_by', '-started_at']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"Call #{self.pk} — Booking #{self.booking_id} ({self.status})"
