"""
AdminNotification model — push notifications created via Truff-Admin.
Supports audience targeting and efficient multicast sending.
"""
from django.db import models
from django.utils import timezone


class AdminNotification(models.Model):
    NOTIFICATION_TYPES = [
        ('promo',        'Promo Code'),
        ('offer',        'Special Offer'),
        ('announcement', 'Announcement'),
        ('booking',      'Booking Reminder'),
        ('test',         'Test'),
    ]
    TARGET_CHOICES = [
        ('all',            'All Users'),
        ('owners',         'Turf Owners'),
        ('customers',      'Customers'),
        ('recent_bookers', 'Recent Bookers (last 30 days)'),
        ('inactive',       'Inactive Users (>30 days)'),
        ('token',          'Single Device (by token)'),
    ]

    title             = models.CharField(max_length=255)
    body              = models.TextField()
    notification_type = models.CharField(max_length=50, choices=NOTIFICATION_TYPES, default='announcement')
    target_type       = models.CharField(max_length=20, choices=TARGET_CHOICES, default='all')

    # Used only when target_type == 'token'
    device_token = models.TextField(blank=True)

    # Optional deep-link payload
    route      = models.CharField(max_length=100, blank=True, help_text='e.g. home, offers, booking')
    promo_code = models.CharField(max_length=20, blank=True)

    # Status / results
    is_sent       = models.BooleanField(default=False)
    sent_at       = models.DateTimeField(null=True, blank=True)
    success_count = models.IntegerField(default=0)
    failure_count = models.IntegerField(default=0)
    error_log     = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Push Notification'

    def __str__(self):
        return f'[{self.notification_type}] {self.title}'

    # ------------------------------------------------------------------
    def get_target_tokens(self):
        """Return list of active FCM tokens for the chosen audience."""
        from users.models import CustomUser
        from datetime import timedelta

        qs = CustomUser.objects.filter(
            is_active=True,
            fcm_token__isnull=False,
        ).exclude(fcm_token='')

        if self.target_type == 'all':
            pass  # already all active users with tokens

        elif self.target_type == 'owners':
            qs = qs.filter(role='turf_owner')

        elif self.target_type == 'customers':
            qs = qs.filter(role='user')

        elif self.target_type == 'recent_bookers':
            from bookings.models import Booking
            cutoff = timezone.now() - timedelta(days=30)
            booker_ids = (
                Booking.objects
                .filter(created_at__gte=cutoff)
                .values_list('user_id', flat=True)
                .distinct()
            )
            qs = qs.filter(id__in=booker_ids)

        elif self.target_type == 'inactive':
            from bookings.models import Booking
            cutoff = timezone.now() - timedelta(days=30)
            active_ids = (
                Booking.objects
                .filter(created_at__gte=cutoff)
                .values_list('user_id', flat=True)
                .distinct()
            )
            qs = qs.exclude(id__in=active_ids)

        elif self.target_type == 'token':
            return [self.device_token] if self.device_token else []

        return list(qs.values_list('fcm_token', flat=True))

    # ------------------------------------------------------------------
    def send(self):
        """
        Dispatch the notification via Firebase multicast.
        Sends up to 500 tokens per batch — no per-device loop needed.
        """
        from .firebase_push import send_to_token, send_multicast

        data = {}
        if self.route:      data['route']     = self.route
        if self.promo_code: data['promo_code'] = self.promo_code

        tokens = self.get_target_tokens()

        if not tokens:
            self.error_log = 'No FCM tokens found for target audience.'
            self.is_sent = True
            self.sent_at = timezone.now()
            self.save()
            return False

        # Split into chunks of 500 (FCM multicast limit)
        total_success = 0
        total_failure = 0
        errors = []

        CHUNK = 500
        for i in range(0, len(tokens), CHUNK):
            chunk = tokens[i:i + CHUNK]
            if len(chunk) == 1:
                result = send_to_token(chunk[0], self.title, self.body, data)
                if result.get('success'):
                    total_success += 1
                else:
                    total_failure += 1
                    errors.append(result.get('error', 'unknown'))
            else:
                result = send_multicast(chunk, self.title, self.body, data)
                if result.get('success'):
                    total_success += result.get('success_count', 0)
                    total_failure += result.get('failure_count', 0)
                else:
                    total_failure += len(chunk)
                    errors.append(result.get('error', 'unknown'))

        self.is_sent = True
        self.sent_at = timezone.now()
        self.success_count = total_success
        self.failure_count = total_failure
        self.error_log = '; '.join(errors[:5])  # keep first 5 errors
        self.save()
        return total_success > 0
