"""
Support app — SupportTicket and SupportMessage models.
"""
import uuid
from django.db import models
from django.utils import timezone
from django.conf import settings

User = settings.AUTH_USER_MODEL


def generate_ticket_id():
    """Generate auto-incrementing ticket ID: TKT-YYYYMMDD-XXXX."""
    today = timezone.now().strftime('%Y%m%d')
    prefix = f'TKT-{today}-'
    last = (
        SupportTicket.objects
        .filter(ticket_id__startswith=prefix)
        .order_by('-ticket_id')
        .values_list('ticket_id', flat=True)
        .first()
    )
    if last:
        try:
            seq = int(last.split('-')[-1]) + 1
        except (ValueError, IndexError):
            seq = 1
    else:
        seq = 1
    return f'{prefix}{seq:04d}'


class SupportTicket(models.Model):
    STATUS_CHOICES = [
        ('open', 'Open'),
        ('awaiting_reply', 'Awaiting Reply'),
        ('resolved', 'Resolved'),
        ('closed', 'Closed'),
    ]
    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('urgent', 'Urgent'),
    ]

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='support_tickets',
    )
    ticket_id = models.CharField(max_length=25, unique=True, editable=False)
    subject = models.CharField(max_length=200)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='medium')
    assigned_to = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='assigned_tickets',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f'{self.ticket_id} — {self.subject}'

    def save(self, *args, **kwargs):
        if not self.ticket_id:
            self.ticket_id = generate_ticket_id()
        super().save(*args, **kwargs)

    @property
    def unread_for_admin(self):
        """Count messages from users that admin hasn't read yet."""
        return self.messages.filter(is_read=False, sender__role='user').count()

    @property
    def last_message(self):
        return self.messages.order_by('-created_at').first()


class SupportMessage(models.Model):
    ticket = models.ForeignKey(
        SupportTicket, on_delete=models.CASCADE, related_name='messages',
    )
    sender = models.ForeignKey(User, on_delete=models.CASCADE)
    message = models.TextField()
    attachment = models.FileField(
        upload_to='support_attachments/', null=True, blank=True,
    )
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'[{self.ticket.ticket_id}] {self.sender.username}: {self.message[:50]}'
