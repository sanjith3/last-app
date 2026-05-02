"""
Email & notification utilities for Truff-Admin turf approval actions.
Falls back to logging if email delivery fails.
"""

import logging
from django.core.mail import send_mail
from django.conf import settings

from .models import AdminConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Email templates — inline strings for simplicity
# ---------------------------------------------------------------------------

TEMPLATES = {
    'approve': {
        'subject': 'Your turf "{name}" has been approved!',
        'body': (
            'Hi {owner_name},\n\n'
            'Great news! Your turf "{name}" has been approved by {admin_name} '
            'on {datetime} and is now visible to the public.\n\n'
            'You can now start receiving bookings.\n\n'
            '— TurfSpotX Team'
        ),
    },
    'reject': {
        'subject': 'Your turf "{name}" was rejected',
        'body': (
            'Hi {owner_name},\n\n'
            'Your turf "{name}" was rejected by {admin_name} on {datetime}.\n\n'
            'Reason: {reason}\n\n'
            'You can edit your turf and resubmit it for review.\n\n'
            '— TurfSpotX Team'
        ),
    },
    'suspend': {
        'subject': 'Your turf "{name}" has been suspended',
        'body': (
            'Hi {owner_name},\n\n'
            'Your turf "{name}" has been suspended by {admin_name} on {datetime}.\n\n'
            'Reason: {reason}\n\n'
            'New bookings are blocked until the suspension is lifted. '
            'If you believe this is an error, please contact support.\n\n'
            '— TurfSpotX Team'
        ),
    },
    'reactivate': {
        'subject': 'Your turf "{name}" has been reactivated!',
        'body': (
            'Hi {owner_name},\n\n'
            'Your turf "{name}" has been reactivated by {admin_name} '
            'on {datetime} and is now visible to the public again.\n\n'
            '— TurfSpotX Team'
        ),
    },
}


def send_turf_status_email(turf, action, admin_name, reason=None):
    """
    Send email notification to turf owner about status change.
    Falls back to logging if email sending fails or owner has no email.
    """
    template = TEMPLATES.get(action)
    if not template:
        logger.warning(f"No email template for action: {action}")
        return False

    owner = turf.owner
    owner_email = getattr(owner, 'email', None)
    owner_name = owner.get_full_name() or owner.username

    from django.utils import timezone
    now_str = timezone.localtime().strftime('%d %b %Y %H:%M')

    context = {
        'name': turf.name,
        'owner_name': owner_name,
        'admin_name': admin_name,
        'datetime': now_str,
        'reason': reason or 'No reason provided',
    }

    subject = template['subject'].format(**context)
    body = template['body'].format(**context)

    if not owner_email:
        logger.info(
            f"[NOTIFICATION] No email for {owner.username}. "
            f"Action: {action}, Turf: {turf.name}\n"
            f"Subject: {subject}\nBody:\n{body}"
        )
        return False

    try:
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@turfspotx.in')
        send_mail(subject, body, from_email, [owner_email], fail_silently=False)
        logger.info(f"[EMAIL] Sent {action} notification to {owner_email} for turf '{turf.name}'")
        return True
    except Exception as e:
        logger.error(f"[EMAIL ERROR] Failed to send {action} email to {owner_email}: {e}")
        # Fallback: log the full message
        logger.info(
            f"[FALLBACK] Subject: {subject}\nBody:\n{body}"
        )
        return False
