"""
Utility functions for TurfZone backend.
Includes Google Maps parsing and geolocation calculations.
"""

import re
from math import radians, cos, sin, asin, sqrt
from urllib.parse import urlparse, parse_qs
import requests


def extract_coordinates_from_google_maps_share_link(share_link: str) -> dict:
    """
    Extract latitude and longitude from a Google Maps share link.
    
    Supports formats:
    - https://maps.app.goo.gl/{shortCode}
    - https://www.google.com/maps/place/{coordinates}
    - Standard Google Maps URLs with @lat,lng
    
    Args:
        share_link (str): Google Maps share link
        
    Returns:
        dict: {'latitude': float, 'longitude': float, 'success': bool, 'message': str}
    """
    try:
        if not share_link:
            return {
                'success': False,
                'message': 'Share link is empty',
                'latitude': None,
                'longitude': None
            }
        
        # Try to extract from standard Google Maps URL format (with @lat,lng)
        at_pattern = r'@(-?\d+\.\d+),(-?\d+\.\d+)'
        match = re.search(at_pattern, share_link)
        if match:
            latitude = float(match.group(1))
            longitude = float(match.group(2))
            return {
                'success': True,
                'message': 'Coordinates extracted successfully',
                'latitude': latitude,
                'longitude': longitude
            }
        
        # Try to expand goo.gl shortener
        if 'maps.app.goo.gl' in share_link:
            try:
                response = requests.head(share_link, allow_redirects=True, timeout=5)
                expanded_url = response.url
                match = re.search(at_pattern, expanded_url)
                if match:
                    latitude = float(match.group(1))
                    longitude = float(match.group(2))
                    return {
                        'success': True,
                        'message': 'Coordinates extracted from expanded URL',
                        'latitude': latitude,
                        'longitude': longitude
                    }
            except Exception as e:
                pass
        
        # Try to extract from query parameters or path
        parsed = urlparse(share_link)
        
        # Check if coordinates are in the path (e.g., /maps/place/lat,lng)
        path_coords = re.search(r'/place/(-?\d+\.\d+),(-?\d+\.\d+)', parsed.path)
        if path_coords:
            latitude = float(path_coords.group(1))
            longitude = float(path_coords.group(2))
            return {
                'success': True,
                'message': 'Coordinates extracted from path',
                'latitude': latitude,
                'longitude': longitude
            }
        
        return {
            'success': False,
            'message': 'Could not extract coordinates from the provided link',
            'latitude': None,
            'longitude': None
        }
        
    except Exception as e:
        return {
            'success': False,
            'message': f'Error parsing link: {str(e)}',
            'latitude': None,
            'longitude': None
        }


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
