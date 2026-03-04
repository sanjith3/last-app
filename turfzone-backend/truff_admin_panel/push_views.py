"""
Push notification views for Truff-Admin.
Appended to the main views.py via import — kept in a separate file for clarity.
"""

import logging
from django.views import View
from django.contrib import messages
from django.shortcuts import render, redirect
from django.db.models import Sum

from .mixins import TruffAdminRequiredMixin as AdminLoginRequiredMixin
from .push_models import AdminNotification
from users.models import CustomUser

logger = logging.getLogger(__name__)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _device_context():
    """Return all stats needed by the Notification Center."""
    from datetime import timedelta
    from django.utils import timezone
    from bookings.models import Booking

    # Users with FCM tokens registered
    base = CustomUser.objects.filter(
        is_active=True, fcm_token__isnull=False
    ).exclude(fcm_token='')

    cutoff = timezone.now() - timedelta(days=30)
    recent_ids = (
        Booking.objects
        .filter(created_at__gte=cutoff)
        .values_list('user_id', flat=True)
        .distinct()
    )

    # Device counts (from CustomUser.fcm_token)
    total_d   = base.count()
    owner_d   = base.filter(role='turf_owner').count()
    cust_d    = base.filter(role='user').count()
    recent_d  = base.filter(id__in=recent_ids).count()
    inact_d   = base.exclude(id__in=recent_ids).count()

    # User counts (all active users regardless of token)
    all_users    = CustomUser.objects.filter(is_active=True)
    total_u      = all_users.count()
    owner_u      = all_users.filter(role='turf_owner').count()
    cust_u       = all_users.filter(role='user').count()
    recent_u     = all_users.filter(id__in=recent_ids).count()
    inactive_u   = all_users.exclude(id__in=recent_ids).count()

    # Notification stats
    total_sent    = AdminNotification.objects.filter(is_sent=True).count()
    total_success = AdminNotification.objects.aggregate(s=Sum('success_count'))['s'] or 0
    total_failure = AdminNotification.objects.aggregate(f=Sum('failure_count'))['f'] or 0
    denom         = total_success + total_failure
    success_rate  = round(total_success / denom * 100, 1) if denom else 0

    return {
        # flat device counts (template uses these directly)
        'total_devices':    total_d,
        'owner_devices':    owner_d,
        'customer_devices': cust_d,
        'recent_devices':   recent_d,
        'inactive_devices': inact_d,

        # nested dict for the pro template
        'device_stats': {
            'total':   total_d,
            'android': 0,   # not tracked per-OS in fcm_token field
            'ios':     0,
            'by_audience': {
                'all':      total_d,
                'owners':   owner_d,
                'customers': cust_d,
                'recent':   recent_d,
                'inactive': inact_d,
            },
        },

        # user counts (may be higher than device counts)
        'audience_stats': {
            'all_users':      total_u,
            'owners':         owner_u,
            'customers':      cust_u,
            'recent_bookers': recent_u,
            'inactive':       inactive_u,
        },

        'stats': {
            'total_sent':    total_sent,
            'total_success': total_success,
            'total_failure': total_failure,
            'success_rate':  success_rate,
            'open_rate':     0,   # would need analytics tracking
        },
    }


# ─── views ────────────────────────────────────────────────────────────────────

class PushNotificationCenterView(AdminLoginRequiredMixin, View):
    """Unified Notification Center — compose + preview + history + stats."""

    def get(self, request):
        ctx = _device_context()
        ctx['recent_notifs'] = AdminNotification.objects.all()[:10]
        ctx['page_title']    = 'Notification Center'
        d = ctx['device_stats']
        ctx['breakdown_rows'] = [
            ('All with app',    d['total'],                      ''),
            ('Turf Owners',     d['by_audience']['owners'],      ''),
            ('Customers',       d['by_audience']['customers'],   ''),
            ('Recent Bookers',  d['by_audience']['recent'],      ''),
            ('Inactive',        d['by_audience']['inactive'],    ''),
        ]
        return render(request, 'truff_admin/push/center.html', ctx)


class PushNotificationListView(AdminLoginRequiredMixin, View):
    """Full notification history list."""

    def get(self, request):
        notifs = AdminNotification.objects.all()[:100]
        return render(request, 'truff_admin/push/list.html', {
            'notifs':      notifs,
            'page_title':  'Push Notification History',
        })


class PushNotificationSendView(AdminLoginRequiredMixin, View):
    """POST handler — create and fire a notification. GET redirects to center."""

    def get(self, request):
        return redirect('truff_admin:push_center')

    def post(self, request):
        title        = request.POST.get('title', '').strip()
        body         = request.POST.get('body', '').strip()
        notif_type   = request.POST.get('notification_type', 'announcement')
        target_type  = request.POST.get('target_type', 'all')
        device_token = request.POST.get('device_token', '').strip()
        route        = request.POST.get('route', '').strip()
        promo_code   = request.POST.get('promo_code', '').strip()

        if not title or not body:
            messages.error(request, 'Title and message body are required.')
            return redirect('truff_admin:push_center')

        notif = AdminNotification.objects.create(
            title=title,
            body=body,
            notification_type=notif_type,
            target_type=target_type,
            device_token=device_token,
            route=route,
            promo_code=promo_code,
        )

        success = notif.send()
        if success:
            messages.success(
                request,
                f'✅ Notification sent! ({notif.success_count} delivered'
                + (f', {notif.failure_count} failed' if notif.failure_count else '')
                + ')',
            )
        else:
            messages.error(
                request,
                f'❌ Notification failed. '
                + (notif.error_log or 'Check Firebase configuration.'),
            )
        return redirect('truff_admin:push_center')


class PushNotificationTestView(AdminLoginRequiredMixin, View):
    """Quick test form — paste a token, fire a test push."""

    def get(self, request):
        return render(request, 'truff_admin/push/test.html', {
            'page_title': 'Test Push Notification',
        })

    def post(self, request):
        token = request.POST.get('device_token', '').strip()
        title = request.POST.get('title', 'Test Notification').strip()
        body  = request.POST.get('body', 'Hello from Truff-Admin! 👋').strip()

        if not token:
            messages.error(request, 'Please paste a device FCM token.')
            return redirect('truff_admin:push_test')

        from .firebase_push import send_to_token
        result = send_to_token(token, title, body, {'route': 'test', 'test': 'true'})

        if result.get('success'):
            messages.success(request, f'✅ Test notification sent! ID: {result["message_id"]}')
        else:
            messages.error(request, f'❌ Send failed: {result.get("error", "unknown error")}')

        return redirect('truff_admin:push_test')
