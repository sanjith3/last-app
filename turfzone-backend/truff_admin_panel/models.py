"""
Truff-Admin models — all additive, no existing model changes.
"""

from django.db import models
from django.conf import settings


# ---------------------------------------------------------------------------
# Admin Audit Log — immutable, append-only
# ---------------------------------------------------------------------------

class AdminAuditLog(models.Model):
    """
    Enterprise-grade immutable audit log.
    Every admin write action creates one row. No delete. No edit.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='truff_audit_logs',
    )
    actor_name = models.CharField(max_length=150, blank=True, default='')
    action = models.CharField(max_length=100, db_index=True)
    target_model = models.CharField(max_length=100, db_index=True)
    target_id = models.IntegerField(default=0, db_index=True)
    details = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Admin Audit Log'
        verbose_name_plural = 'Admin Audit Logs'
        indexes = [
            models.Index(fields=['action', 'created_at']),
            models.Index(fields=['target_model', 'target_id']),
        ]

    def __str__(self):
        return f"[{self.created_at:%Y-%m-%d %H:%M}] {self.actor_name}: {self.action} on {self.target_model}#{self.target_id}"

    def delete(self, *args, **kwargs):
        raise PermissionError("Audit logs cannot be deleted.")

    def save(self, *args, **kwargs):
        # Only allow creation, not updates
        if self.pk is not None:
            raise PermissionError("Audit logs cannot be modified.")
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# PIN Change Request
# ---------------------------------------------------------------------------

class PinChangeRequestStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    APPROVED = 'approved', 'Approved'
    REJECTED = 'rejected', 'Rejected'


class PinChangeRequest(models.Model):
    """PIN change request from a turf owner, requiring admin review."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='pin_change_requests',
    )
    new_pin_hash = models.CharField(max_length=255)
    status = models.CharField(
        max_length=20,
        choices=PinChangeRequestStatus.choices,
        default=PinChangeRequestStatus.PENDING,
        db_index=True,
    )
    admin = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='resolved_pin_requests',
    )
    notes = models.TextField(blank=True, default='')
    phone_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'created_at']),
        ]

    def __str__(self):
        return f"PinChangeRequest #{self.pk} [{self.status}] for {self.owner}"


# ---------------------------------------------------------------------------
# Admin Config — dynamic business settings
# ---------------------------------------------------------------------------

class AdminConfig(models.Model):
    """
    Key-value store for admin-configurable business settings.
    e.g. platform_fee_percent=5, flat_transaction_fee=10
    """

    key = models.CharField(max_length=100, unique=True)
    value = models.TextField(default='')
    description = models.CharField(max_length=255, blank=True, default='')
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Admin Config'
        verbose_name_plural = 'Admin Configs'

    def __str__(self):
        return f"{self.key} = {self.value}"

    @classmethod
    def get(cls, key, default=''):
        """Get config value by key with fallback default."""
        try:
            return cls.objects.get(key=key).value
        except cls.DoesNotExist:
            return default
