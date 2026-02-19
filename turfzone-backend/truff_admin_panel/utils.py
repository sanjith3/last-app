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
