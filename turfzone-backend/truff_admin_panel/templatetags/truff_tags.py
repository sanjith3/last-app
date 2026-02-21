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
