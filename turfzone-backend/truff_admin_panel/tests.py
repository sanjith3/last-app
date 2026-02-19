"""
Truff-Admin tests — security, audit, export, and isolation checks.
"""

from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from .models import AdminAuditLog, PinChangeRequest, AdminConfig

User = get_user_model()


class TruffAdminAccessTest(TestCase):
    """Verify only staff/group users can access admin."""

    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(
            username='truffadmin', password='testpass123', is_staff=True
        )
        self.regular = User.objects.create_user(
            username='regularuser', password='testpass123', role='user'
        )

    def test_unauthenticated_redirects_to_login(self):
        resp = self.client.get('/truff-admin/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/truff-admin/login/', resp.url)

    def test_non_staff_gets_forbidden(self):
        self.client.login(username='regularuser', password='testpass123')
        resp = self.client.get('/truff-admin/')
        self.assertIn(resp.status_code, [302, 403])

    def test_staff_can_access_dashboard(self):
        self.client.login(username='truffadmin', password='testpass123')
        resp = self.client.get('/truff-admin/')
        self.assertEqual(resp.status_code, 200)

    def test_group_member_can_access(self):
        group, _ = Group.objects.get_or_create(name='Truff Admin')
        user = User.objects.create_user(
            username='groupuser', password='testpass123'
        )
        user.groups.add(group)
        self.client.login(username='groupuser', password='testpass123')
        resp = self.client.get('/truff-admin/')
        self.assertEqual(resp.status_code, 200)


class AuditLogTest(TestCase):
    """Test audit log immutability."""

    def test_audit_log_cannot_be_deleted(self):
        log = AdminAuditLog.objects.create(
            action='test_action', target_model='Test', target_id=1
        )
        with self.assertRaises(PermissionError):
            log.delete()

    def test_audit_log_cannot_be_modified(self):
        log = AdminAuditLog.objects.create(
            action='test_action', target_model='Test', target_id=1
        )
        log.action = 'modified'
        with self.assertRaises(PermissionError):
            log.save()


class AdminConfigTest(TestCase):
    """Test config store."""

    def test_get_with_default(self):
        val = AdminConfig.get('nonexistent', 'fallback')
        self.assertEqual(val, 'fallback')

    def test_get_existing(self):
        AdminConfig.objects.create(key='test_key', value='42')
        self.assertEqual(AdminConfig.get('test_key'), '42')


class PublicAPIIsolationTest(TestCase):
    """Ensure public API endpoints are NOT affected."""

    def test_turf_api_unchanged(self):
        resp = self.client.get('/api/turfs/turfs/')
        # Should return 200 or 401 (auth required), NOT 404
        self.assertNotEqual(resp.status_code, 404)

    def test_booking_api_unchanged(self):
        resp = self.client.get('/api/bookings/bookings/my_bookings/')
        self.assertNotEqual(resp.status_code, 404)


# ═══════════════════════════════════════════════════════════════════════════
# TURF APPROVAL SYSTEM TESTS
# ═══════════════════════════════════════════════════════════════════════════

from turfs.models import Turf, TurfStatus, Sport
import json


class TurfApprovalTestMixin:
    """Shared setup for turf approval tests."""

    def _setup_turf_data(self):
        self.admin = User.objects.create_user(
            username='admin_user', password='testpass', is_staff=True, role='admin'
        )
        self.owner = User.objects.create_user(
            username='owner_user', password='testpass', role='turf_owner'
        )
        self.regular = User.objects.create_user(
            username='regular_user', password='testpass', role='user'
        )
        self.sport = Sport.objects.create(name='Football')
        self.turf = Turf.objects.create(
            name='Test Turf', owner=self.owner,
            address='Test Address', city='TestCity', state='TestState',
            latitude=11.0, longitude=76.8, price_per_hour=500,
        )
        self.turf.sports.add(self.sport)
        self.client = Client()


class TurfCreationStatusTest(TurfApprovalTestMixin, TestCase):
    """New turfs default to PENDING status."""

    def setUp(self):
        self._setup_turf_data()

    def test_new_turf_is_pending(self):
        self.assertEqual(self.turf.status, TurfStatus.PENDING)
        self.assertFalse(self.turf.is_active)

    def test_approved_at_is_null(self):
        self.assertIsNone(self.turf.approved_at)

    def test_approved_by_is_null(self):
        self.assertIsNone(self.turf.approved_by)


class AdminApprovalFlowTest(TurfApprovalTestMixin, TestCase):
    """Admin approve/reject/suspend/reactivate actions."""

    def setUp(self):
        self._setup_turf_data()
        self.client.login(username='admin_user', password='testpass')

    def test_approve_sets_correct_status(self):
        resp = self.client.post(
            f'/truff-admin/turfs/{self.turf.id}/action/',
            {'action': 'approve'}
        )
        self.turf.refresh_from_db()
        self.assertEqual(self.turf.status, TurfStatus.APPROVED)
        self.assertTrue(self.turf.is_active)
        self.assertIsNotNone(self.turf.approved_at)
        self.assertEqual(self.turf.approved_by, self.admin)

    def test_approve_creates_audit_log(self):
        self.client.post(
            f'/truff-admin/turfs/{self.turf.id}/action/',
            {'action': 'approve'}
        )
        log = AdminAuditLog.objects.filter(
            target_model='Turf', target_id=self.turf.id, action='turf_approved'
        )
        self.assertTrue(log.exists())

    def test_reject_requires_reason(self):
        resp = self.client.post(
            f'/truff-admin/turfs/{self.turf.id}/action/',
            {'action': 'reject'}
        )
        self.assertEqual(resp.status_code, 400)

    def test_reject_sets_status_and_reason(self):
        self.client.post(
            f'/truff-admin/turfs/{self.turf.id}/action/',
            {'action': 'reject', 'reason': 'Incomplete info'}
        )
        self.turf.refresh_from_db()
        self.assertEqual(self.turf.status, TurfStatus.REJECTED)
        self.assertFalse(self.turf.is_active)
        self.assertEqual(self.turf.rejection_reason, 'Incomplete info')

    def test_suspend_sets_status_and_reason(self):
        # First approve, then suspend
        self.turf.approve(admin=self.admin)
        self.client.post(
            f'/truff-admin/turfs/{self.turf.id}/action/',
            {'action': 'suspend', 'reason': 'Policy violation'}
        )
        self.turf.refresh_from_db()
        self.assertEqual(self.turf.status, TurfStatus.SUSPENDED)
        self.assertFalse(self.turf.is_active)
        self.assertEqual(self.turf.suspend_reason, 'Policy violation')

    def test_reactivate_sets_approved(self):
        self.turf.suspend(reason='test')
        self.client.post(
            f'/truff-admin/turfs/{self.turf.id}/action/',
            {'action': 'reactivate'}
        )
        self.turf.refresh_from_db()
        self.assertEqual(self.turf.status, TurfStatus.APPROVED)
        self.assertTrue(self.turf.is_active)
        self.assertEqual(self.turf.approved_by, self.admin)

    def test_idempotent_approve(self):
        """Approving an already-approved turf is a no-op redirect."""
        self.turf.approve(admin=self.admin)
        initial_count = AdminAuditLog.objects.filter(action='turf_approved').count()
        resp = self.client.post(
            f'/truff-admin/turfs/{self.turf.id}/action/',
            {'action': 'approve'}
        )
        self.assertEqual(resp.status_code, 302)
        # No new audit log
        self.assertEqual(
            AdminAuditLog.objects.filter(action='turf_approved').count(),
            initial_count
        )

    def test_non_admin_cannot_access(self):
        self.client.login(username='regular_user', password='testpass')
        resp = self.client.post(
            f'/truff-admin/turfs/{self.turf.id}/action/',
            {'action': 'approve'}
        )
        self.assertIn(resp.status_code, [302, 403])


class TurfStatusPublicAPITest(TurfApprovalTestMixin, TestCase):
    """Only APPROVED turfs visible in public API."""

    def setUp(self):
        self._setup_turf_data()

    def test_pending_turf_not_in_public_list(self):
        resp = self.client.get('/api/turfs/turfs/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        turf_ids = [t['id'] for t in data.get('results', [])]
        self.assertNotIn(self.turf.id, turf_ids)

    def test_approved_turf_in_public_list(self):
        self.turf.approve(admin=self.admin)
        resp = self.client.get('/api/turfs/turfs/')
        data = resp.json()
        turf_ids = [t['id'] for t in data.get('results', [])]
        self.assertIn(self.turf.id, turf_ids)

    def test_suspended_turf_not_in_public_list(self):
        self.turf.approve(admin=self.admin)
        self.turf.suspend(reason='test')
        resp = self.client.get('/api/turfs/turfs/')
        data = resp.json()
        turf_ids = [t['id'] for t in data.get('results', [])]
        self.assertNotIn(self.turf.id, turf_ids)


class OwnerResubmitTest(TurfApprovalTestMixin, TestCase):
    """Owner can resubmit rejected turf."""

    def setUp(self):
        self._setup_turf_data()

    def test_owner_resubmit_rejected_turf(self):
        from rest_framework.test import APIClient
        api = APIClient()
        api.force_authenticate(user=self.owner)

        # Reject first
        self.turf.reject(reason='Bad photos')
        self.assertEqual(self.turf.status, TurfStatus.REJECTED)

        resp = api.post(f'/api/turfs/turfs/{self.turf.id}/resubmit/')
        self.assertEqual(resp.status_code, 200)
        self.turf.refresh_from_db()
        self.assertEqual(self.turf.status, TurfStatus.PENDING)
        self.assertIsNone(self.turf.rejection_reason)

    def test_cannot_resubmit_approved_turf(self):
        from rest_framework.test import APIClient
        api = APIClient()
        api.force_authenticate(user=self.owner)

        self.turf.approve(admin=self.admin)
        resp = api.post(f'/api/turfs/turfs/{self.turf.id}/resubmit/')
        self.assertEqual(resp.status_code, 400)

    def test_non_owner_cannot_resubmit(self):
        from rest_framework.test import APIClient
        api = APIClient()
        api.force_authenticate(user=self.regular)

        self.turf.reject(reason='test')
        resp = api.post(f'/api/turfs/turfs/{self.turf.id}/resubmit/')
        # 404 because get_queryset() hides turfs not owned by this user
        self.assertIn(resp.status_code, [403, 404])


class BulkActionTest(TurfApprovalTestMixin, TestCase):
    """Test bulk approve/reject."""

    def setUp(self):
        self._setup_turf_data()
        self.client.login(username='admin_user', password='testpass')
        self.turf2 = Turf.objects.create(
            name='Test Turf 2', owner=self.owner,
            address='Addr 2', city='TestCity', state='TestState',
            latitude=11.1, longitude=76.9, price_per_hour=600,
        )

    def test_bulk_approve(self):
        resp = self.client.post(
            '/truff-admin/turfs/bulk-action/',
            {
                'action': 'approve',
                'turf_ids': json.dumps([self.turf.id, self.turf2.id]),
            }
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['processed'], 2)
        self.turf.refresh_from_db()
        self.turf2.refresh_from_db()
        self.assertEqual(self.turf.status, TurfStatus.APPROVED)
        self.assertEqual(self.turf2.status, TurfStatus.APPROVED)

    def test_bulk_reject_requires_reason(self):
        resp = self.client.post(
            '/truff-admin/turfs/bulk-action/',
            {
                'action': 'reject',
                'turf_ids': json.dumps([self.turf.id]),
            }
        )
        self.assertEqual(resp.status_code, 400)

    def test_bulk_reject_with_reason(self):
        resp = self.client.post(
            '/truff-admin/turfs/bulk-action/',
            {
                'action': 'reject',
                'reason': 'Missing documents',
                'turf_ids': json.dumps([self.turf.id, self.turf2.id]),
            }
        )
        data = resp.json()
        self.assertEqual(data['processed'], 2)
        self.turf.refresh_from_db()
        self.assertEqual(self.turf.status, TurfStatus.REJECTED)
        self.assertEqual(self.turf.rejection_reason, 'Missing documents')


class ImageURLTest(TurfApprovalTestMixin, TestCase):
    """Verify image URLs returned by API are absolute."""

    def setUp(self):
        self._setup_turf_data()

    def test_image_serializer_returns_absolute_url(self):
        """TurfImageSerializer must return http:// URLs, not relative /media/ paths."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from rest_framework.test import APIClient
        from turfs.models import TurfImage

        # Create a minimal GIF image
        image_content = (
            b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00'
            b'\x80\x00\x00\xff\xff\xff\x00\x00\x00'
            b'\x21\xf9\x04\x00\x00\x00\x00\x00'
            b'\x2c\x00\x00\x00\x00\x01\x00\x01\x00'
            b'\x00\x02\x02\x44\x01\x00\x3b'
        )
        uploaded = SimpleUploadedFile('test.gif', image_content, content_type='image/gif')
        TurfImage.objects.create(turf=self.turf, image=uploaded, is_cover=True)

        self.turf.approve(admin=self.admin)

        # Fetch via DRF API client (provides request context)
        api = APIClient()
        resp = api.get(f'/api/turfs/turfs/{self.turf.id}/')
        self.assertEqual(resp.status_code, 200)
        response_json = resp.json()
        # retrieve() wraps in {'success': True, 'data': {...}}
        data = response_json.get('data', response_json)

        # cover_image must be absolute
        cover_url = data.get('cover_image')
        self.assertIsNotNone(cover_url, 'cover_image should not be None')
        self.assertTrue(
            cover_url.startswith('http'),
            f'cover_image must be absolute URL, got: {cover_url}'
        )

        # images list entries must be absolute
        images = data.get('images', [])
        self.assertTrue(len(images) > 0, 'images list should not be empty')
        for img in images:
            self.assertTrue(
                img['image'].startswith('http'),
                f'Image URL must be absolute, got: {img["image"]}'
            )


class AuditLogOnApproveTest(TurfApprovalTestMixin, TestCase):
    """Verify REST API approve creates audit log."""

    def setUp(self):
        self._setup_turf_data()

    def test_rest_api_approve_creates_audit_log(self):
        from rest_framework.test import APIClient
        api = APIClient()
        api.force_authenticate(user=self.admin)
        resp = api.post(f'/api/turfs/turfs/{self.turf.id}/approve/')
        self.assertEqual(resp.status_code, 200)

        log = AdminAuditLog.objects.filter(
            action='turf_approved', target_model='Turf', target_id=self.turf.id
        )
        self.assertTrue(log.exists(), 'Audit log entry should be created on approve')
