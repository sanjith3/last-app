"""
Custom permissions for TurfSpotX.
"""

from rest_framework.permissions import BasePermission


class IsPlatformAdmin(BasePermission):
    """
    Only allows access to users with role == 'admin'.
    Does NOT fall back to Django's is_staff — checks our custom role field.
    """

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and getattr(request.user, 'role', None) == 'admin'
        )
