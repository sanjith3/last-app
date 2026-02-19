"""
Tests for multi-turf owner registration.

Covers:
  - Existing phone reuse (creates new turf, no error)
  - New phone (creates user + turf)
  - Role upgrade on become_partner
  - Serializer allows repeat registrations
"""
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status as http_status
from unittest.mock import patch

from users.models import TurfOwner
from turfs.models import Turf, TurfStatus

User = get_user_model()

# Mock coordinates to avoid actual Google Maps API calls
MOCK_COORDS_SUCCESS = {'success': True, 'latitude': 11.0, 'longitude': 76.9, 'message': 'ok'}
MOCK_COORDS_FAIL = {'success': False, 'latitude': None, 'longitude': None, 'message': 'Invalid link'}
GOOGLE_MAPS_LINK = 'https://maps.app.goo.gl/testlink123'
COORDS_PATCH = 'users.views.extract_coordinates_from_google_maps_share_link'


class TurfOwnerRegistrationTests(TestCase):
    """Tests for POST /api/users/user-registration/turf_owner_register/"""

    def setUp(self):
        self.client = APIClient()
        self.url = '/api/users/user-registration/turf_owner_register/'

    def _reg_data(self, phone='9994463000', username='newowner', email='new@test.com'):
        return {
            'username': username,
            'email': email,
            'password': 'TestPass123',
            'password_confirm': 'TestPass123',
            'first_name': 'Test',
            'last_name': 'Owner',
            'phone_number': phone,
            'google_maps_share_link': GOOGLE_MAPS_LINK,
            'turf_name': 'Test Arena',
            'city': 'Coimbatore',
            'state': 'Tamil Nadu',
        }

    @patch(COORDS_PATCH, return_value=MOCK_COORDS_SUCCESS)
    def test_new_phone_creates_user_and_turf(self, mock_coords):
        """Brand new phone number → creates CustomUser + Turf."""
        resp = self.client.post(self.url, self._reg_data(), format='json')
        self.assertEqual(resp.status_code, http_status.HTTP_201_CREATED)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertIn('turf_id', data)
        self.assertIn('tokens', data)

        # Verify DB
        user = User.objects.get(phone_number='9994463000')
        self.assertEqual(user.role, 'turf_owner')
        self.assertEqual(user.turfs.count(), 1)
        turf = user.turfs.first()
        self.assertEqual(turf.status, TurfStatus.PENDING)
        self.assertFalse(turf.is_active)

    @patch(COORDS_PATCH, return_value=MOCK_COORDS_SUCCESS)
    def test_existing_phone_reuses_user_creates_turf(self, mock_coords):
        """Phone already exists → reuse user, create 2nd turf, NO error."""
        # Create existing owner
        existing = User.objects.create_user(
            username='existingowner', email='existing@test.com',
            password='Pass123', phone_number='9876543210', role='turf_owner',
        )
        TurfOwner.objects.create(user=existing)
        Turf.objects.create(
            owner=existing, name='Turf 1', latitude=11.0, longitude=76.9,
            price_per_hour=500, status=TurfStatus.APPROVED, is_active=True,
        )
        self.assertEqual(existing.turfs.count(), 1)

        # Register again with SAME phone
        resp = self.client.post(self.url, self._reg_data(
            phone='9876543210', username='ignored', email='ignored@test.com',
        ), format='json')

        self.assertEqual(resp.status_code, http_status.HTTP_201_CREATED)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertIn('New turf added', data['message'])
        self.assertEqual(data['total_turfs'], 2)

        # DB verification: still one user, now two turfs
        self.assertEqual(User.objects.filter(phone_number='9876543210').count(), 1)
        existing.refresh_from_db()
        self.assertEqual(existing.turfs.count(), 2)
        self.assertEqual(existing.role, 'turf_owner')

    @patch(COORDS_PATCH, return_value=MOCK_COORDS_SUCCESS)
    def test_existing_phone_upgrades_regular_user_role(self, mock_coords):
        """Regular user phone → auto-upgrade role to turf_owner."""
        regular = User.objects.create_user(
            username='regularuser', email='regular@test.com',
            password='Pass123', phone_number='1111111111', role='user',
        )
        self.assertEqual(regular.role, 'user')

        resp = self.client.post(self.url, self._reg_data(
            phone='1111111111', username='ignored2', email='ignored2@test.com',
        ), format='json')

        self.assertEqual(resp.status_code, http_status.HTTP_201_CREATED)
        regular.refresh_from_db()
        self.assertEqual(regular.role, 'turf_owner')
        self.assertEqual(regular.turfs.count(), 1)

    @patch(COORDS_PATCH, return_value=MOCK_COORDS_FAIL)
    def test_existing_owner_not_deleted_on_coord_failure(self, mock_coords):
        """If coords fail for existing owner, user must NOT be deleted."""
        existing = User.objects.create_user(
            username='safeowner', email='safe@test.com',
            password='Pass123', phone_number='2222222222', role='turf_owner',
        )
        TurfOwner.objects.create(user=existing)

        resp = self.client.post(self.url, self._reg_data(
            phone='2222222222', username='x', email='x@test.com',
        ), format='json')

        self.assertEqual(resp.status_code, http_status.HTTP_400_BAD_REQUEST)
        # User must still exist
        self.assertTrue(User.objects.filter(id=existing.id).exists())

    @patch(COORDS_PATCH, return_value=MOCK_COORDS_FAIL)
    def test_new_user_deleted_on_coord_failure(self, mock_coords):
        """If coords fail for brand new user, user IS deleted."""
        resp = self.client.post(self.url, self._reg_data(
            phone='3333333333', username='tempuser', email='temp@test.com',
        ), format='json')

        self.assertEqual(resp.status_code, http_status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.filter(phone_number='3333333333').exists())

    @patch(COORDS_PATCH, return_value=MOCK_COORDS_SUCCESS)
    def test_no_phone_blocking_validation_error(self, mock_coords):
        """Ensure no 'already registered' error for duplicate phones."""
        User.objects.create_user(
            username='owner1', email='o1@test.com',
            password='Pass123', phone_number='4444444444', role='turf_owner',
        )
        resp = self.client.post(self.url, self._reg_data(
            phone='4444444444', username='owner2', email='o2@test.com',
        ), format='json')

        # Must NOT return 400 with phone error
        self.assertEqual(resp.status_code, http_status.HTTP_201_CREATED)
        data = resp.json()
        self.assertTrue(data['success'])
        # Must not contain any phone-related error
        errors = data.get('errors', {})
        self.assertNotIn('phone_number', errors)
        self.assertNotIn('phone', str(data).lower().replace('phone_number', ''))


class BecomePartnerTests(TestCase):
    """Tests for POST /api/users/user-profile/become_partner/"""

    def setUp(self):
        self.client = APIClient()
        self.url = '/api/users/user-profile/become_partner/'

    def _partner_data(self):
        return {
            'google_maps_share_link': GOOGLE_MAPS_LINK,
            'turf_name': 'Partner Arena',
            'city': 'Chennai',
            'state': 'Tamil Nadu',
        }

    @patch(COORDS_PATCH, return_value=MOCK_COORDS_SUCCESS)
    def test_become_partner_upgrades_role(self, mock_coords):
        """Regular user calling become_partner → role upgraded to turf_owner."""
        user = User.objects.create_user(
            username='regularpartner', email='rp@test.com',
            password='Pass123', phone_number='5555555555', role='user',
        )
        self.client.force_authenticate(user=user)

        resp = self.client.post(self.url, self._partner_data(), format='json')
        self.assertEqual(resp.status_code, http_status.HTTP_201_CREATED)

        user.refresh_from_db()
        self.assertEqual(user.role, 'turf_owner')
        self.assertEqual(user.turfs.count(), 1)
        self.assertTrue(TurfOwner.objects.filter(user=user).exists())

    @patch(COORDS_PATCH, return_value=MOCK_COORDS_SUCCESS)
    def test_become_partner_allows_multiple_turfs(self, mock_coords):
        """Existing owner calling become_partner again → creates 2nd turf."""
        user = User.objects.create_user(
            username='multiowner', email='mo@test.com',
            password='Pass123', phone_number='6666666666', role='turf_owner',
        )
        TurfOwner.objects.create(user=user)
        Turf.objects.create(
            owner=user, name='First Turf', latitude=11.0, longitude=76.9,
            price_per_hour=500, status=TurfStatus.APPROVED, is_active=True,
        )
        self.client.force_authenticate(user=user)

        resp = self.client.post(self.url, self._partner_data(), format='json')
        self.assertEqual(resp.status_code, http_status.HTTP_201_CREATED)
        data = resp.json()
        self.assertEqual(data['total_turfs'], 2)
        self.assertIn('2 turfs', data['message'])

    @patch(COORDS_PATCH, return_value=MOCK_COORDS_SUCCESS)
    def test_admin_role_not_downgraded(self, mock_coords):
        """Admin calling become_partner → role stays admin, turf still created."""
        admin = User.objects.create_user(
            username='adminpartner', email='ap@test.com',
            password='Pass123', phone_number='7777777777', role='admin',
        )
        self.client.force_authenticate(user=admin)

        resp = self.client.post(self.url, self._partner_data(), format='json')
        self.assertEqual(resp.status_code, http_status.HTTP_201_CREATED)

        admin.refresh_from_db()
        self.assertEqual(admin.role, 'admin')  # NOT downgraded
        self.assertEqual(admin.turfs.count(), 1)

    @patch(COORDS_PATCH, return_value=MOCK_COORDS_SUCCESS)
    def test_turf_created_as_pending(self, mock_coords):
        """All new turfs must be PENDING and is_active=False."""
        user = User.objects.create_user(
            username='pendingcheck', email='pc@test.com',
            password='Pass123', phone_number='8888888888', role='user',
        )
        self.client.force_authenticate(user=user)

        resp = self.client.post(self.url, self._partner_data(), format='json')
        self.assertEqual(resp.status_code, http_status.HTTP_201_CREATED)

        turf = Turf.objects.get(id=resp.json()['turf_id'])
        self.assertEqual(turf.status, TurfStatus.PENDING)
        self.assertFalse(turf.is_active)
