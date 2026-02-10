"""
Booking models for TurfZone backend.
"""

from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()


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


class Booking(models.Model):
    """Turf booking model."""
    
    # Relationships
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='bookings')
    turf = models.ForeignKey('turfs.Turf', on_delete=models.CASCADE, related_name='bookings')
    
    # Booking Details
    booking_date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    
    # Pricing
    price_per_hour = models.IntegerField()
    total_price = models.DecimalField(max_digits=8, decimal_places=2)
    discount = models.DecimalField(max_digits=8, decimal_places=2, default=0.00)
    final_price = models.DecimalField(max_digits=8, decimal_places=2)
    
    # Status
    booking_status = models.CharField(
        max_length=20,
        choices=BookingStatus.choices,
        default=BookingStatus.PENDING
    )
    payment_status = models.CharField(
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING
    )
    
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
        """Cancel this booking."""
        self.booking_status = BookingStatus.CANCELLED
        self.cancelled_reason = reason
        self.cancelled_at = timezone.now()
        self.cancelled_by_admin = cancelled_by_admin
        self.save()
    
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
        ]


class Payment(models.Model):
    """Payment tracking for bookings."""
    
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name='payment')
    
    # Payment Details
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    payment_method = models.CharField(max_length=50, default='card')  # card, upi, bank, etc.
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
