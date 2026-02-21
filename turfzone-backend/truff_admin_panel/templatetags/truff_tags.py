"""
Custom template tags for Truff-Admin templates.
"""

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def pretty_json(value):
    """Format a JSONField dict as readable key: value lines."""
    if not value or value == {}:
        return mark_safe('&mdash;')
    if isinstance(value, dict):
        parts = []
        for k, v in value.items():
            key = str(k).replace('_', ' ').title()
            parts.append(f'<b>{key}:</b> {v}')
        return mark_safe('<br>'.join(parts))
    return str(value)


@register.filter
def currency(value):
    """Format a number as Indian Rupee currency."""
    try:
        val = float(value)
        if val >= 10_000_000:
            return f"₹{val / 10_000_000:.2f} Cr"
        if val >= 100_000:
            return f"₹{val / 100_000:.2f} L"
        return f"₹{val:,.2f}"
    except (ValueError, TypeError):
        return f"₹0.00"


@register.filter
def status_badge(status):
    """Return CSS class for status badges."""
    mapping = {
        'approved': 'badge-success',
        'active': 'badge-success',
        'confirmed': 'badge-success',
        'completed': 'badge-success',
        'paid': 'badge-success',
        'pending': 'badge-warning',
        'processing': 'badge-warning',
        'rejected': 'badge-danger',
        'cancelled': 'badge-danger',
        'failed': 'badge-danger',
        'suspended': 'badge-danger',
    }
    return mapping.get(str(status).lower(), 'badge-secondary')


@register.filter
def format_action(action):
    """Convert action slug to human-readable text."""
    action_map = {
        'turf_approved': 'Turf Approved',
        'turf_rejected': 'Turf Rejected',
        'turf_suspended': 'Turf Suspended',
        'turf_reactivated': 'Turf Reactivated',
        'future_bookings_cancelled': 'Future Bookings Cancelled',
        'offer_created': 'Offer Created',
        'offer_updated': 'Offer Updated',
        'offer_deleted': 'Offer Deleted',
        'admin_login': 'Admin Login',
        'admin_logout': 'Admin Logout',
    }
    return action_map.get(str(action), str(action).replace('_', ' ').title())


@register.filter
def format_audit_details(details):
    """Convert audit log details dict to readable text."""
    if not details:
        return '-'

    if isinstance(details, str):
        import json
        try:
            details = json.loads(details)
        except (json.JSONDecodeError, ValueError):
            return details

    if not isinstance(details, dict):
        return str(details)

    parts = []
    turf = details.get('turf_name')
    if turf:
        parts.append(f"Turf: {turf}")
    reason = details.get('reason')
    if reason:
        parts.append(f"Reason: {reason}")
    count = details.get('count')
    if count:
        parts.append(f"{count} future bookings cancelled")
    username = details.get('username')
    if username:
        parts.append(f"User: {username}")

    if parts:
        return ' — '.join(parts)

    # Fallback: show all key-value pairs nicely
    return ', '.join(f"{k.replace('_', ' ').title()}: {v}" for k, v in details.items())
