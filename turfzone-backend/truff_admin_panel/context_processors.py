"""
Template context processors for Truff-Admin.
"""

from turfs.models import Turf, TurfStatus


def pending_turf_count(request):
    """Inject pending turf count into all templates for sidebar badge."""
    if not request.user.is_authenticated:
        return {}
    if not (request.user.is_staff or request.user.groups.filter(name='Truff Admin').exists()):
        return {}
    try:
        return {'pending_turf_count': Turf.objects.filter(status=TurfStatus.PENDING).count()}
    except Exception:
        return {'pending_turf_count': 0}
