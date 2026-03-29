"""
Utility helpers for Truff-Admin.
"""

from .models import AdminAuditLog


def get_client_ip(request):
    """Extract client IP from request."""
    x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded:
        return x_forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '0.0.0.0')


def log_admin_action(request, action, target_model, target_id, details=None):
    """
    Create an immutable audit log entry for an admin action.
    """
    user = request.user if request.user.is_authenticated else None
    AdminAuditLog.objects.create(
        actor=user,
        actor_name=str(user) if user else 'anonymous',
        action=action,
        target_model=target_model,
        target_id=target_id,
        details=details or {},
        ip_address=get_client_ip(request),
    )


# ---------------------------------------------------------------------------
# BUG-B FIX: Content sanitisation for admin HTML templates
# ---------------------------------------------------------------------------

# Tags that are safe to render as HTML in the admin (informational only)
_ALLOWED_TAGS = ['b', 'i', 'u', 'em', 'strong', 'p', 'br', 'ul', 'ol', 'li']
_ALLOWED_ATTRIBUTES: dict = {}


def sanitize_text(text: str) -> str:
    """
    Sanitize user-generated text for safe rendering in admin HTML templates.

    Uses ``bleach.clean()`` when the package is installed, stripping all
    tags not in _ALLOWED_TAGS and all HTML attributes.  Falls back to
    Django's ``html.escape()`` if bleach is unavailable so the app never
    crashes on missing optional dependency.

    Usage in views::

        context['turf_description'] = sanitize_text(turf.description)

    Usage in templates::

        {{ turf_description|safe }}   {# bleach already stripped dangerous tags #}
    """
    if not text:
        return text or ''
    try:
        import bleach
        return bleach.clean(
            text,
            tags=_ALLOWED_TAGS,
            attributes=_ALLOWED_ATTRIBUTES,
            strip=True,
        )
    except ImportError:
        # bleach not installed — fall back to plain HTML escaping
        from django.utils.html import escape
        return escape(text)
