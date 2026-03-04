"""
Firebase Admin SDK helper — used by truff_admin_panel for push notifications.
Initialises the app lazily (safe to import at module level).
"""

import logging
import firebase_admin
from firebase_admin import credentials, messaging
from django.conf import settings

logger = logging.getLogger(__name__)

_app = None


def _get_app():
    """Return (and lazily initialise) the Firebase Admin app."""
    global _app
    if _app is not None:
        return _app

    cred_path = getattr(settings, 'FIREBASE_SERVICE_ACCOUNT_PATH', None)
    if not cred_path:
        raise RuntimeError(
            'FIREBASE_SERVICE_ACCOUNT_PATH is not set in settings.py. '
            'Download the service-account JSON from Firebase Console → Project Settings → Service Accounts.'
        )

    cred = credentials.Certificate(cred_path)
    try:
        _app = firebase_admin.get_app()          # already initialised
    except ValueError:
        _app = firebase_admin.initialize_app(cred)

    logger.info('Firebase Admin SDK initialised.')
    return _app


# ─────────────────────────────────────────────────────────────────────────────
# Public helpers
# ─────────────────────────────────────────────────────────────────────────────

def send_to_token(token: str, title: str, body: str, data: dict = None) -> dict:
    """Send a push notification to a single FCM registration token."""
    _get_app()
    message = messaging.Message(
        notification=messaging.Notification(title=title, body=body),
        data={k: str(v) for k, v in (data or {}).items()},
        android=messaging.AndroidConfig(priority='high'),
        token=token,
    )
    try:
        resp = messaging.send(message)
        logger.info(f'FCM send_to_token OK: {resp}')
        return {'success': True, 'message_id': resp}
    except Exception as exc:
        logger.error(f'FCM send_to_token FAILED: {exc}')
        return {'success': False, 'error': str(exc)}


def send_to_topic(topic: str, title: str, body: str, data: dict = None) -> dict:
    """Broadcast to all devices subscribed to a topic (e.g. 'all_users')."""
    _get_app()
    message = messaging.Message(
        notification=messaging.Notification(title=title, body=body),
        data={k: str(v) for k, v in (data or {}).items()},
        android=messaging.AndroidConfig(priority='high'),
        topic=topic,
    )
    try:
        resp = messaging.send(message)
        logger.info(f'FCM send_to_topic "{topic}" OK: {resp}')
        return {'success': True, 'message_id': resp}
    except Exception as exc:
        logger.error(f'FCM send_to_topic FAILED: {exc}')
        return {'success': False, 'error': str(exc)}


def send_multicast(tokens: list, title: str, body: str, data: dict = None) -> dict:
    """Send to up to 500 tokens at once using MulticastMessage."""
    if not tokens:
        return {'success': True, 'success_count': 0, 'failure_count': 0}
    _get_app()
    message = messaging.MulticastMessage(
        notification=messaging.Notification(title=title, body=body),
        data={k: str(v) for k, v in (data or {}).items()},
        android=messaging.AndroidConfig(priority='high'),
        tokens=tokens,
    )
    try:
        resp = messaging.send_each_for_multicast(message)
        return {
            'success': True,
            'success_count': resp.success_count,
            'failure_count': resp.failure_count,
        }
    except Exception as exc:
        logger.error(f'FCM send_multicast FAILED: {exc}')
        return {'success': False, 'error': str(exc)}
