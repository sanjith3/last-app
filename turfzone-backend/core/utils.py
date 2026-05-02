"""
Utility functions for TurfSpotX backend.
Includes Google Maps parsing and geolocation calculations.
"""

import ipaddress
import json
import logging
import re
import socket
import uuid
from math import radians, cos, sin, asin, sqrt
from urllib.parse import urlparse, parse_qs, unquote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger('core.maps')

# ─── CONFIGURATION ───

# Hosts we trust for redirect resolution
ALLOWED_HOSTS = frozenset({
    'maps.app.goo.gl',
    'goo.gl',
    'www.google.com',
    'google.com',
    'maps.google.com',
    'maps.google.co.in',
    'maps.google.co.uk',
})

# Browser-like User-Agent for redirect resolution
_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/120.0.0.0 Safari/537.36'
)

MAX_REDIRECTS = 5
HEAD_TIMEOUT = 3
GET_TIMEOUT = 6

# User-facing guidance message
_FAIL_MESSAGE = (
    'Could not extract coordinates from link. '
    'Please tap Google Maps \u2192 Share \u2192 Copy link, '
    'and paste that link, or paste coordinates as "lat,long".'
)

# TODO: Set to False after initial verification period
MAPS_DEBUG_LOGGING = True


# ─── SSRF PROTECTION ───

def _is_private_ip(hostname: str) -> bool:
    """Check if a hostname resolves to a private/loopback IP."""
    try:
        ip = socket.gethostbyname(hostname)
        return ipaddress.ip_address(ip).is_private
    except (socket.gaierror, ValueError):
        return True  # can't resolve → treat as unsafe


def _host_allowed(url: str) -> bool:
    """Check if URL host is in the trusted whitelist."""
    try:
        host = urlparse(url).hostname
        if not host:
            return False
        host = host.lower()
        # Exact match or *.google.co.XX pattern
        if host in ALLOWED_HOSTS:
            return True
        if host.startswith('maps.google.co.') or host.startswith('maps.google.com.'):
            return True
        return False
    except Exception:
        return False


# ─── URL NORMALIZATION ───

def _normalize_url(raw: str) -> str:
    """Trim, decode, ensure scheme."""
    url = raw.strip()
    url = unquote(url)
    if url and not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    return url


# ─── REDIRECT RESOLUTION ───

def _resolve_short_link(url: str, debug_id: str) -> str | None:
    """
    Follow redirects for short Google Maps links.
    Returns the final resolved URL, or None on failure.
    """
    host = urlparse(url).hostname or ''
    if not _host_allowed(url):
        logger.info('[MAPS:%s] skip_resolution host=%s not_in_whitelist', debug_id, host)
        return None

    if _is_private_ip(host):
        logger.warning('[MAPS:%s] SSRF_BLOCKED host=%s resolves_to_private_ip', debug_id, host)
        return None

    session = requests.Session()
    retries = Retry(total=1, backoff_factor=0.3, status_forcelist=[502, 503, 504])
    session.mount('https://', HTTPAdapter(max_retries=retries))
    session.mount('http://', HTTPAdapter(max_retries=retries))

    headers = {'User-Agent': _USER_AGENT}

    # Attempt 1: HEAD with redirect following
    try:
        resp = session.head(
            url, allow_redirects=True, timeout=HEAD_TIMEOUT,
            headers=headers,
        )
        final_url = resp.url
        logger.info('[MAPS:%s] HEAD status=%d final_url=%s', debug_id, resp.status_code, final_url)

        # Validate final host
        final_host = urlparse(final_url).hostname or ''
        if not _host_allowed(final_url) and not final_host.endswith('.google.com'):
            logger.warning('[MAPS:%s] redirected_to_untrusted_host=%s', debug_id, final_host)
            return None

        if _is_private_ip(final_host):
            logger.warning('[MAPS:%s] SSRF_BLOCKED final_host=%s private_ip', debug_id, final_host)
            return None

        if resp.status_code < 400:
            return final_url
    except requests.RequestException as e:
        logger.info('[MAPS:%s] HEAD failed: %s, trying GET', debug_id, str(e)[:120])

    # Attempt 2: GET fallback (some servers block HEAD)
    try:
        resp = session.get(
            url, allow_redirects=True, timeout=GET_TIMEOUT,
            headers=headers, stream=True,  # don't download full body
        )
        final_url = resp.url
        logger.info('[MAPS:%s] GET status=%d final_url=%s', debug_id, resp.status_code, final_url)
        resp.close()

        final_host = urlparse(final_url).hostname or ''
        if not _host_allowed(final_url) and not final_host.endswith('.google.com'):
            logger.warning('[MAPS:%s] GET redirected_to_untrusted_host=%s', debug_id, final_host)
            return None

        if resp.status_code < 400:
            return final_url
    except requests.RequestException as e:
        logger.info('[MAPS:%s] GET also failed: %s', debug_id, str(e)[:120])

    return None


# ─── COORDINATE PARSERS (ordered by likelihood) ───

def _validate_coords(lat: float, lon: float) -> bool:
    """Validate coordinate ranges."""
    return -90 <= lat <= 90 and -180 <= lon <= 180


def _try_at_pattern(url: str) -> tuple[float, float, str] | None:
    """Pattern 1: @lat,long (most common in resolved Google Maps URLs)."""
    m = re.search(r'@(-?\d+\.?\d*),(-?\d+\.?\d*)', url)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if _validate_coords(lat, lon):
            return lat, lon, '@lat,long'
    return None


def _try_query_q(url: str) -> tuple[float, float, str] | None:
    """Pattern 2: ?q=lat,long or ?q=lat,long(Place)."""
    parsed = urlparse(url)
    q_vals = parse_qs(parsed.query).get('q', [])
    for q in q_vals:
        m = re.match(r'(-?\d+\.?\d*),\s*(-?\d+\.?\d*)', q.strip())
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))
            if _validate_coords(lat, lon):
                return lat, lon, '?q=lat,long'
    return None


def _try_place_path(url: str) -> tuple[float, float, str] | None:
    """Pattern 3: /place/.../@lat,long or /place/lat,long."""
    m = re.search(r'/place/[^/]*/@(-?\d+\.?\d*),(-?\d+\.?\d*)', url)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if _validate_coords(lat, lon):
            return lat, lon, '/place/@lat,long'
    # Also try /place/lat,long directly
    m = re.search(r'/place/(-?\d+\.?\d*),(-?\d+\.?\d*)', url)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if _validate_coords(lat, lon):
            return lat, lon, '/place/lat,long'
    return None


def _try_data_tokens(url: str) -> tuple[float, float, str] | None:
    """Pattern 4: !3dlat!4dlong tokens (older Google URLs)."""
    m_lat = re.search(r'!3d(-?\d+\.?\d*)', url)
    m_lon = re.search(r'!4d(-?\d+\.?\d*)', url)
    if m_lat and m_lon:
        lat, lon = float(m_lat.group(1)), float(m_lon.group(1))
        if _validate_coords(lat, lon):
            return lat, lon, '!3d!4d_tokens'
    return None


def _try_search_path(url: str) -> tuple[float, float, str] | None:
    """Pattern 5: /maps/search/lat,long."""
    m = re.search(r'/maps/search/(-?\d+\.?\d*),(-?\d+\.?\d*)', url)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if _validate_coords(lat, lon):
            return lat, lon, '/maps/search/lat,long'
    return None


def _try_dir_path(url: str) -> tuple[float, float, str] | None:
    """Pattern 6: /maps/dir/?...destination=lat,long."""
    parsed = urlparse(url)
    dest = parse_qs(parsed.query).get('destination', [])
    for d in dest:
        m = re.match(r'(-?\d+\.?\d*),\s*(-?\d+\.?\d*)', d.strip())
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))
            if _validate_coords(lat, lon):
                return lat, lon, 'dir?destination=lat,long'
    return None


def _try_raw_coords(text: str) -> tuple[float, float, str] | None:
    """Pattern 7: Raw 'lat, long' or 'lat,long' pasted directly."""
    text = text.strip()
    m = re.match(r'^(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)$', text)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if _validate_coords(lat, lon):
            return lat, lon, 'raw_lat_long'
    return None


def _try_center_param(text: str) -> tuple[float, float, str] | None:
    """Pattern 8: center=lat%2Clong or center=lat,long (static map URLs in HTML)."""
    # URL-encoded comma: %2C
    m = re.search(r'center=(-?\d+\.?\d*)%2C(-?\d+\.?\d*)', text)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if _validate_coords(lat, lon):
            return lat, lon, 'center_param'
    # Plain comma version
    m = re.search(r'center=(-?\d+\.?\d*),(-?\d+\.?\d*)', text)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if _validate_coords(lat, lon):
            return lat, lon, 'center_param'
    return None


# Ordered list of parsers to try against URL strings
_PARSERS = [
    _try_at_pattern,
    _try_query_q,
    _try_place_path,
    _try_data_tokens,
    _try_search_path,
    _try_dir_path,
    _try_center_param,
    _try_raw_coords,
]

# Additional parsers for HTML body (includes center= which is most common)
_HTML_PARSERS = [
    _try_at_pattern,
    _try_center_param,
    _try_data_tokens,
    _try_query_q,
]


def _extract_from_url(url: str, debug_id: str) -> tuple[float, float, str] | None:
    """Try all parsers against a URL string."""
    for parser in _PARSERS:
        result = parser(url)
        if result:
            lat, lon, method = result
            logger.info('[MAPS:%s] EXTRACTED lat=%s lon=%s method=%s', debug_id, lat, lon, method)
            return result
    return None


def _extract_from_html_body(url: str, debug_id: str) -> tuple[float, float, str] | None:
    """
    Fetch the HTML page at a Google Maps URL and scan for coordinate patterns.
    Used when the URL itself does not contain coordinates (place-ID URLs).
    """
    if not _host_allowed(url) and not (urlparse(url).hostname or '').endswith('.google.com'):
        logger.info('[MAPS:%s] html_fetch skipped: untrusted host', debug_id)
        return None

    headers = {'User-Agent': _USER_AGENT}
    try:
        resp = requests.get(url, timeout=GET_TIMEOUT, headers=headers, stream=True)
        # Read only first 200KB to avoid memory abuse
        body = resp.content[:200_000].decode('utf-8', errors='replace')
        resp.close()
        logger.info('[MAPS:%s] html_fetch status=%d body_len=%d', debug_id, resp.status_code, len(body))
    except requests.RequestException as e:
        logger.info('[MAPS:%s] html_fetch failed: %s', debug_id, str(e)[:120])
        return None

    # Try each HTML parser against the page body
    for parser in _HTML_PARSERS:
        result = parser(body)
        if result:
            lat, lon, method = result
            logger.info('[MAPS:%s] HTML_EXTRACTED lat=%s lon=%s method=%s', debug_id, lat, lon, method)
            return lat, lon, f'html_{method}'

    logger.info('[MAPS:%s] html_body has no coord patterns', debug_id)
    return None


# ─── MAIN ENTRY POINT ───

def extract_coordinates_from_google_maps_share_link(share_link: str) -> dict:
    """
    Extract latitude and longitude from a Google Maps share link.

    Supports:
    - Short links: maps.app.goo.gl, goo.gl/maps
    - Standard: @lat,long / ?q=lat,long / /place/ / !3d!4d / /search/ / /dir/
    - Raw: lat,long text

    Returns:
        dict: {'latitude': float, 'longitude': float, 'success': bool,
               'message': str, 'debug_id': str}
    """
    debug_id = str(uuid.uuid4())[:8]
    fail = lambda msg: {
        'success': False, 'message': msg,
        'latitude': None, 'longitude': None, 'debug_id': debug_id,
    }

    if not share_link or not share_link.strip():
        logger.info('[MAPS:%s] empty_link', debug_id)
        return fail('Share link is empty')

    original = share_link.strip()
    url = _normalize_url(original)

    if MAPS_DEBUG_LOGGING:
        logger.info('[MAPS:%s] original=%s normalized=%s', debug_id, original, url)

    # Step 1: Try parsing the raw/normalized URL directly
    result = _extract_from_url(url, debug_id)
    if result:
        lat, lon, method = result
        return {
            'success': True,
            'message': f'Coordinates extracted ({method})',
            'latitude': lat, 'longitude': lon, 'debug_id': debug_id,
        }

    # Step 2: If it's a short link, resolve redirects
    host = (urlparse(url).hostname or '').lower()
    needs_resolution = (
        'goo.gl' in host
        or 'maps.app' in host
        or len(urlparse(url).path.strip('/')) < 20  # short path = likely shortener
    )

    if needs_resolution and _host_allowed(url):
        resolved = _resolve_short_link(url, debug_id)
        if resolved and resolved != url:
            result = _extract_from_url(resolved, debug_id)
            if result:
                lat, lon, method = result
                return {
                    'success': True,
                    'message': f'Coordinates extracted from resolved URL ({method})',
                    'latitude': lat, 'longitude': lon, 'debug_id': debug_id,
                }
            else:
                logger.info('[MAPS:%s] resolved URL has no coords, trying HTML body fetch...', debug_id)
                # Step 2.5: Fetch HTML body and scan for coords
                body_result = _extract_from_html_body(resolved, debug_id)
                if body_result:
                    lat, lon, method = body_result
                    return {
                        'success': True,
                        'message': f'Coordinates extracted from page content ({method})',
                        'latitude': lat, 'longitude': lon, 'debug_id': debug_id,
                    }
        else:
            logger.info('[MAPS:%s] resolution_failed or same_url', debug_id)

    # Step 3: Try raw lat,long as last resort (in case user pasted just coords)
    result = _try_raw_coords(original)
    if result:
        lat, lon, method = result
        logger.info('[MAPS:%s] raw_fallback lat=%s lon=%s', debug_id, lat, lon)
        return {
            'success': True,
            'message': f'Coordinates extracted ({method})',
            'latitude': lat, 'longitude': lon, 'debug_id': debug_id,
        }

    logger.warning('[MAPS:%s] FAILED original=%s', debug_id, original[:200])
    return fail(_FAIL_MESSAGE)


def calculate_distance_haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great circle distance between two points 
    on the earth (specified in decimal degrees).
    
    Returns distance in kilometers.
    
    Args:
        lat1 (float): Latitude of first point
        lon1 (float): Longitude of first point
        lat2 (float): Latitude of second point
        lon2 (float): Longitude of second point
        
    Returns:
        float: Distance in kilometers
    """
    try:
        # Convert decimal degrees to radians
        lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
        
        # Haversine formula
        dlon = lon2 - lon1
        dlat = lat2 - lat1
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        c = 2 * asin(sqrt(a))
        
        # Radius of earth in kilometers
        r = 6371
        
        distance = c * r
        return round(distance, 2)
    except Exception as e:
        return 0.0


def find_nearby_turfs(user_latitude: float, user_longitude: float, 
                     turfs: list, radius_km: float = 50) -> list:
    """
    Find turfs within a specified radius of the user's location.
    Results are sorted by distance (nearest first).
    
    Args:
        user_latitude (float): User's latitude
        user_longitude (float): User's longitude
        turfs (list): List of turf dictionaries with 'latitude' and 'longitude' keys
        radius_km (float): Search radius in kilometers (default: 50 km)
        
    Returns:
        list: Sorted list of nearby turfs with 'distance' field added
    """
    nearby_turfs = []
    
    for turf in turfs:
        try:
            turf_lat = turf.get('latitude')
            turf_lon = turf.get('longitude')
            
            if turf_lat is None or turf_lon is None:
                continue
            
            distance = calculate_distance_haversine(
                user_latitude, user_longitude,
                turf_lat, turf_lon
            )
            
            if distance <= radius_km:
                turf_copy = turf.copy()
                turf_copy['distance'] = distance
                nearby_turfs.append(turf_copy)
        except Exception as e:
            continue
    
    # Sort by distance
    nearby_turfs.sort(key=lambda x: x.get('distance', float('inf')))
    
    return nearby_turfs


def get_google_maps_directions_url(latitude: float, longitude: float) -> str:
    """
    Generate a Google Maps directions URL for a given location.
    
    Args:
        latitude (float): Turf latitude
        longitude (float): Turf longitude
        
    Returns:
        str: Google Maps directions URL
    """
    return f"https://www.google.com/maps/dir/?api=1&destination={latitude},{longitude}"
