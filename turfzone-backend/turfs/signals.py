"""
Cache invalidation signals for turfs app.

Automatically clears the owner_dashboard_stats Redis cache whenever a
Booking or Turf record is saved or deleted.  This ensures the dashboard
never shows stale data for more than OWNER_DASHBOARD_CACHE_TTL seconds.

Connected in TurfsConfig.ready() (turfs/apps.py).
"""

import logging
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.core.cache import cache

logger = logging.getLogger(__name__)


def _invalidate_owner_cache(owner_id):
    """Delete a single owner's dashboard cache key."""
    if not owner_id:
        return
    key = f'owner_dashboard_stats_{owner_id}'
    try:
        cache.delete(key)
        logger.debug('Cache invalidated: %s', key)
    except Exception as exc:
        logger.warning('Cache invalidation failed for %s: %s', key, exc)


# ── Booking signals ───────────────────────────────────────────────────────────

@receiver(post_save, sender='bookings.Booking')
def booking_saved(sender, instance, **kwargs):
    """Invalidate owner cache when a booking is created, updated, or cancelled."""
    try:
        owner_id = instance.turf.owner_id
        _invalidate_owner_cache(owner_id)
    except Exception:
        pass  # instance.turf may not exist yet during migrations


@receiver(post_delete, sender='bookings.Booking')
def booking_deleted(sender, instance, **kwargs):
    """Invalidate owner cache when a booking is hard-deleted."""
    try:
        owner_id = instance.turf.owner_id
        _invalidate_owner_cache(owner_id)
    except Exception:
        pass


# ── Turf signals ─────────────────────────────────────────────────────────────

@receiver(post_save, sender='turfs.Turf')
def turf_saved(sender, instance, **kwargs):
    """Invalidate owner cache when turf status / shutdown state changes."""
    _invalidate_owner_cache(instance.owner_id)


@receiver(post_delete, sender='turfs.Turf')
def turf_deleted(sender, instance, **kwargs):
    """Invalidate owner cache when a turf is deleted."""
    _invalidate_owner_cache(instance.owner_id)
