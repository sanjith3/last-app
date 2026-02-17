"""
Finance models — LedgerEntry and OwnerSettlement.
"""

from django.db import models
from django.conf import settings
from decimal import Decimal


class LedgerAccount(models.TextChoices):
    """Chart of accounts for double-entry ledger."""
    CUSTOMER_PAYMENT = 'customer_payment', 'Customer Payment'
    SLOT_REVENUE = 'slot_revenue', 'Slot Revenue'
    GST_COLLECTED = 'gst_collected', 'GST Collected'
    PLATFORM_FEE_REVENUE = 'platform_fee_revenue', 'Platform Fee Revenue'
    GST_ON_PLATFORM_FEE = 'gst_on_platform_fee', 'GST on Platform Fee'
    COMMISSION_REVENUE = 'commission_revenue', 'Commission Revenue'
    OWNER_PAYABLE = 'owner_payable', 'Owner Payable'


class EntryType(models.TextChoices):
    DEBIT = 'debit', 'Debit'
    CREDIT = 'credit', 'Credit'


class LedgerEntry(models.Model):
    """
    Double-entry ledger row. Every booking creates multiple entries.
    Invariant: sum(debit) == sum(credit) per booking.
    """
    booking = models.ForeignKey(
        'bookings.Booking',
        on_delete=models.PROTECT,
        related_name='ledger_entries',
    )
    entry_type = models.CharField(max_length=6, choices=EntryType.choices)
    account = models.CharField(max_length=30, choices=LedgerAccount.choices)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'Ledger Entries'
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['booking', 'entry_type']),
            models.Index(fields=['account', 'created_at']),
        ]

    def __str__(self):
        return f"{self.entry_type.upper()} {self.account} ₹{self.amount} (Booking #{self.booking_id})"


class SettlementStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    PROCESSING = 'processing', 'Processing'
    COMPLETED = 'completed', 'Completed'
    FAILED = 'failed', 'Failed'


class OwnerSettlement(models.Model):
    """Settlement record for paying turf owners."""
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='settlements',
    )
    period_start = models.DateField()
    period_end = models.DateField()
    total_gross = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    total_commission = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    total_gst = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    net_payout = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    razorpay_transfer_id = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(
        max_length=20,
        choices=SettlementStatus.choices,
        default=SettlementStatus.PENDING,
    )
    settled_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['owner', 'status']),
            models.Index(fields=['period_start', 'period_end']),
        ]

    def __str__(self):
        return f"Settlement {self.owner} [{self.period_start} → {self.period_end}] ₹{self.net_payout}"
