"""
Comprehensive tests for Google Maps coordinate extraction.
Tests all 7 URL patterns, redirect resolution, SSRF protection, and edge cases.
"""

from unittest.mock import patch, MagicMock
from django.test import TestCase

from core.utils import (
    extract_coordinates_from_google_maps_share_link,
    _try_at_pattern,
    _try_query_q,
    _try_place_path,
    _try_data_tokens,
    _try_search_path,
    _try_dir_path,
    _try_raw_coords,
    _validate_coords,
    _host_allowed,
    _normalize_url,
)


class CoordinateValidationTests(TestCase):
    """Test coordinate range validation."""

    def test_valid_coords(self):
        self.assertTrue(_validate_coords(12.345, 76.987))
        self.assertTrue(_validate_coords(-90, -180))
        self.assertTrue(_validate_coords(90, 180))
        self.assertTrue(_validate_coords(0, 0))

    def test_invalid_lat(self):
        self.assertFalse(_validate_coords(91, 0))
        self.assertFalse(_validate_coords(-91, 0))

    def test_invalid_lon(self):
        self.assertFalse(_validate_coords(0, 181))
        self.assertFalse(_validate_coords(0, -181))


class URLNormalizationTests(TestCase):
    """Test URL normalization."""

    def test_adds_scheme(self):
        self.assertEqual(_normalize_url('maps.app.goo.gl/abc'), 'https://maps.app.goo.gl/abc')

    def test_preserves_https(self):
        url = 'https://www.google.com/maps'
        self.assertEqual(_normalize_url(url), url)

    def test_trims_whitespace(self):
        self.assertEqual(
            _normalize_url('  https://google.com/maps  '),
            'https://google.com/maps',
        )

    def test_decodes_percent(self):
        self.assertIn('@', _normalize_url('https://google.com/maps/%40'))


class HostWhitelistTests(TestCase):
    """Test SSRF host whitelist."""

    def test_allowed_hosts(self):
        self.assertTrue(_host_allowed('https://maps.app.goo.gl/abc'))
        self.assertTrue(_host_allowed('https://goo.gl/maps/abc'))
        self.assertTrue(_host_allowed('https://www.google.com/maps'))
        self.assertTrue(_host_allowed('https://maps.google.com/maps'))
        self.assertTrue(_host_allowed('https://maps.google.co.in/maps'))

    def test_blocked_hosts(self):
        self.assertFalse(_host_allowed('https://evil.com/maps'))
        self.assertFalse(_host_allowed('https://192.168.1.1/maps'))
        self.assertFalse(_host_allowed('https://localhost/maps'))

    def test_empty_url(self):
        self.assertFalse(_host_allowed(''))
        self.assertFalse(_host_allowed('not-a-url'))


class AtPatternTests(TestCase):
    """Pattern 1: @lat,long"""

    def test_standard_url(self):
        url = 'https://www.google.com/maps/place/Marathahalli/@12.9563408,77.7017005,17z'
        result = _try_at_pattern(url)
        self.assertIsNotNone(result)
        lat, lon, method = result
        self.assertAlmostEqual(lat, 12.9563408, places=5)
        self.assertAlmostEqual(lon, 77.7017005, places=5)
        self.assertEqual(method, '@lat,long')

    def test_negative_coords(self):
        url = 'https://www.google.com/maps/@-33.8688197,151.2092955,15z'
        result = _try_at_pattern(url)
        self.assertIsNotNone(result)
        lat, lon, _ = result
        self.assertAlmostEqual(lat, -33.8688197)
        self.assertAlmostEqual(lon, 151.2092955)

    def test_no_match(self):
        self.assertIsNone(_try_at_pattern('https://google.com/maps'))


class QueryQTests(TestCase):
    """Pattern 2: ?q=lat,long"""

    def test_basic_q(self):
        url = 'https://www.google.com/maps?q=12.345678,76.987654'
        result = _try_query_q(url)
        self.assertIsNotNone(result)
        lat, lon, method = result
        self.assertAlmostEqual(lat, 12.345678)
        self.assertAlmostEqual(lon, 76.987654)
        self.assertEqual(method, '?q=lat,long')

    def test_q_with_place_suffix(self):
        url = 'https://www.google.com/maps?q=12.345678,76.987654(Some+Place)'
        # parse_qs will include the (Some+Place) part, but our regex only extracts leading coords
        result = _try_query_q(url)
        self.assertIsNotNone(result)

    def test_no_q(self):
        self.assertIsNone(_try_query_q('https://google.com/maps'))


class PlacePathTests(TestCase):
    """Pattern 3: /place/.../@lat,long"""

    def test_place_with_at(self):
        url = 'https://www.google.com/maps/place/Some+Place/@12.345678,76.987654,17z'
        result = _try_place_path(url)
        self.assertIsNotNone(result)
        lat, lon, method = result
        self.assertAlmostEqual(lat, 12.345678)
        self.assertEqual(method, '/place/@lat,long')

    def test_place_direct_coords(self):
        url = 'https://www.google.com/maps/place/12.345678,76.987654'
        result = _try_place_path(url)
        self.assertIsNotNone(result)


class DataTokenTests(TestCase):
    """Pattern 4: !3dlat!4dlong"""

    def test_data_tokens(self):
        url = 'https://www.google.com/maps/place/data=!3m1!4b1!3d12.345678!4d76.987654'
        result = _try_data_tokens(url)
        self.assertIsNotNone(result)
        lat, lon, method = result
        self.assertAlmostEqual(lat, 12.345678)
        self.assertAlmostEqual(lon, 76.987654)
        self.assertEqual(method, '!3d!4d_tokens')

    def test_no_tokens(self):
        self.assertIsNone(_try_data_tokens('https://google.com/maps'))


class SearchPathTests(TestCase):
    """Pattern 5: /maps/search/lat,long"""

    def test_search_path(self):
        url = 'https://www.google.com/maps/search/12.345678,76.987654'
        result = _try_search_path(url)
        self.assertIsNotNone(result)
        lat, lon, method = result
        self.assertAlmostEqual(lat, 12.345678)
        self.assertEqual(method, '/maps/search/lat,long')


class DirPathTests(TestCase):
    """Pattern 6: /maps/dir/?destination=lat,long"""

    def test_dir_destination(self):
        url = 'https://www.google.com/maps/dir/?api=1&destination=12.345678,76.987654'
        result = _try_dir_path(url)
        self.assertIsNotNone(result)
        lat, lon, method = result
        self.assertAlmostEqual(lat, 12.345678)
        self.assertEqual(method, 'dir?destination=lat,long')


class RawCoordsTests(TestCase):
    """Pattern 7: raw lat,long text"""

    def test_raw_coords(self):
        result = _try_raw_coords('12.345678, 76.987654')
        self.assertIsNotNone(result)
        lat, lon, method = result
        self.assertAlmostEqual(lat, 12.345678)
        self.assertEqual(method, 'raw_lat_long')

    def test_raw_no_space(self):
        result = _try_raw_coords('12.345678,76.987654')
        self.assertIsNotNone(result)

    def test_negative_raw(self):
        result = _try_raw_coords('-33.868, 151.209')
        self.assertIsNotNone(result)
        lat, lon, _ = result
        self.assertAlmostEqual(lat, -33.868)

    def test_not_raw_url(self):
        """A full URL should not match raw pattern."""
        self.assertIsNone(_try_raw_coords('https://google.com/maps'))


class MainFunctionTests(TestCase):
    """Integration tests for the main extract function."""

    def test_empty_link(self):
        result = extract_coordinates_from_google_maps_share_link('')
        self.assertFalse(result['success'])
        self.assertIn('debug_id', result)

    def test_none_link(self):
        result = extract_coordinates_from_google_maps_share_link(None)
        self.assertFalse(result['success'])

    def test_standard_at_url(self):
        url = 'https://www.google.com/maps/place/Marathahalli/@12.9563408,77.7017005,17z'
        result = extract_coordinates_from_google_maps_share_link(url)
        self.assertTrue(result['success'])
        self.assertAlmostEqual(result['latitude'], 12.9563408)
        self.assertAlmostEqual(result['longitude'], 77.7017005)
        self.assertIn('debug_id', result)

    def test_query_q_url(self):
        url = 'https://www.google.com/maps?q=12.345678,76.987654'
        result = extract_coordinates_from_google_maps_share_link(url)
        self.assertTrue(result['success'])

    def test_data_token_url(self):
        url = 'https://www.google.com/maps/place/data=!3m1!4b1!3d12.345678!4d76.987654'
        result = extract_coordinates_from_google_maps_share_link(url)
        self.assertTrue(result['success'])
        self.assertAlmostEqual(result['latitude'], 12.345678)

    def test_raw_coords(self):
        result = extract_coordinates_from_google_maps_share_link('12.345678, 76.987654')
        self.assertTrue(result['success'])

    def test_no_scheme_url(self):
        """URL without https:// should still work."""
        result = extract_coordinates_from_google_maps_share_link(
            'www.google.com/maps?q=12.345678,76.987654'
        )
        self.assertTrue(result['success'])

    def test_failure_returns_guidance(self):
        result = extract_coordinates_from_google_maps_share_link('https://example.com/notamap')
        self.assertFalse(result['success'])
        self.assertIn('Share', result['message'])
        self.assertIn('Copy link', result['message'])

    @patch('core.utils._resolve_short_link')
    def test_short_link_resolution(self, mock_resolve):
        """Simulate short link resolving to a full URL with coordinates."""
        mock_resolve.return_value = (
            'https://www.google.com/maps/place/Some+Place/@12.9563408,77.7017005,17z'
        )
        result = extract_coordinates_from_google_maps_share_link(
            'https://maps.app.goo.gl/9TK8gNkbkhrxZoQA6'
        )
        self.assertTrue(result['success'])
        self.assertAlmostEqual(result['latitude'], 12.9563408)
        mock_resolve.assert_called_once()

    @patch('core.utils._resolve_short_link')
    def test_short_link_resolution_failure(self, mock_resolve):
        """If short link resolution fails, return actionable error."""
        mock_resolve.return_value = None
        result = extract_coordinates_from_google_maps_share_link(
            'https://maps.app.goo.gl/invalidXYZ'
        )
        self.assertFalse(result['success'])
        self.assertIn('Copy link', result['message'])

    @patch('core.utils._resolve_short_link')
    def test_goo_gl_maps_link(self, mock_resolve):
        """goo.gl/maps/ links should also attempt resolution."""
        mock_resolve.return_value = (
            'https://www.google.com/maps/@10.123,77.456,15z'
        )
        result = extract_coordinates_from_google_maps_share_link(
            'https://goo.gl/maps/ABCDEFG'
        )
        self.assertTrue(result['success'])


class RedirectChainMockTest(TestCase):
    """Integration test: mock full HTTP redirect chain."""

    @patch('core.utils.requests.Session')
    def test_redirect_chain(self, MockSessionClass):
        """Simulate maps.app.goo.gl → consent → final maps URL."""
        mock_session = MagicMock()
        MockSessionClass.return_value = mock_session

        # HEAD returns the final resolved URL
        mock_head_resp = MagicMock()
        mock_head_resp.status_code = 200
        mock_head_resp.url = 'https://www.google.com/maps/place/MyTurf/@13.0827,80.2707,17z'
        mock_session.head.return_value = mock_head_resp

        result = extract_coordinates_from_google_maps_share_link(
            'https://maps.app.goo.gl/XYZABC123'
        )
        self.assertTrue(result['success'])
        self.assertAlmostEqual(result['latitude'], 13.0827, places=3)
        self.assertAlmostEqual(result['longitude'], 80.2707, places=3)
