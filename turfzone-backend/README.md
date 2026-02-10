# TurfZone Backend - Django REST API

A comprehensive Django REST Framework backend for the TurfZone turf booking application.

## Features

- ✅ Custom user authentication with role-based access (Normal User, Turf Owner, Platform Admin)
- ✅ Google Maps share link parsing to automatically extract coordinates
- ✅ Haversine distance calculation for nearby turf discovery
- ✅ Turf registration with approval workflow
- ✅ Booking management system
- ✅ Review and rating system
- ✅ JWT token-based authentication
- ✅ Complete REST API with DRF

## Project Structure

```
turfzone-backend/
├── manage.py
├── requirements.txt
├── turfzone/                  # Main project settings
│   ├── settings.py           # Django configuration
│   ├── urls.py               # Main URL router
│   └── wsgi.py               # WSGI application
├── core/                      # Shared utilities
│   └── utils.py              # Google Maps parsing, distance calculation
├── users/                     # User authentication & profiles
│   ├── models.py             # CustomUser, TurfOwner models
│   ├── views.py              # Authentication views, user endpoints
│   ├── serializers.py        # User serializers
│   ├── urls.py               # User routes
│   └── admin.py              # Django admin configuration
├── turfs/                     # Turf management
│   ├── models.py             # Turf, TurfImage, Sport, Amenity, Review
│   ├── views.py              # Turf viewsets and endpoints
│   ├── serializers.py        # Turf serializers
│   ├── urls.py               # Turf routes
│   └── admin.py              # Django admin configuration
└── bookings/                 # Booking management
    ├── models.py             # Booking, Payment models
    ├── views.py              # Booking viewsets and endpoints
    ├── serializers.py        # Booking serializers
    ├── urls.py               # Booking routes
    └── admin.py              # Django admin configuration
```

## Installation

### 1. Create Virtual Environment

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS/Linux
python3 -m venv venv
source venv/bin/activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Database Setup

```bash
# Apply migrations
python manage.py makemigrations
python manage.py migrate

# Create superuser (admin)
python manage.py createsuperuser
```

### 4. **Important: Create Initial Data**

```bash
# Run this command to create Sports and Amenities in database
python manage.py shell

# In the Django shell, run:
from turfs.models import Sport, Amenity

# Create Sports
sports = [
    'Cricket',
    'Football',
    'Badminton',
    'Tennis',
    'Basketball',
    'Volleyball',
    'Kabaddi',
]
for sport_name in sports:
    Sport.objects.get_or_create(name=sport_name)

# Create Amenities
amenities = [
    'Flood Lights',
    'Parking',
    'Water',
    'WiFi',
    'Cafeteria',
    'Changing Rooms',
    'Showers',
    'Lockers',
    'First Aid',
    'CCTV',
    'Equipment Rental',
    'Seating Area',
    'Gym',
]
for amenity_name in amenities:
    Amenity.objects.get_or_create(name=amenity_name)

# Exit shell
exit()
```

### 5. Run Development Server

```bash
python manage.py runserver
```

Sever will be running at: `http://127.0.0.1:8000`

## API Endpoints

### Authentication
- **POST** `/api/users/user-registration/normal_user_register/` - Register normal user
- **POST** `/api/users/user-registration/turf_owner_register/` - Register turf owner (with Google Maps link)
- **POST** `/api/users/user-login/login/` - Login user

### User Profile
- **GET** `/api/users/user-profile/me/` - Get current user profile
- **PUT** `/api/users/user-profile/update_profile/` - Update profile
- **POST** `/api/users/user-profile/change_password/` - Change password

### Turfs
- **GET** `/api/turfs/turfs/` - List turfs (with distance calculation if lat/lng provided)
  - Query params: `latitude`, `longitude`, `radius`, `search`, `city`, `min_price`, `max_price`
- **POST** `/api/turfs/turfs/` - Create turf (turf owner only)
- **GET** `/api/turfs/turfs/{id}/` - Get turf details
- **PUT/PATCH** `/api/turfs/turfs/{id}/` - Update turf
- **POST** `/api/turfs/turfs/{id}/approve/` - Approve turf (admin only)
- **POST** `/api/turfs/turfs/{id}/reject/` - Reject turf (admin only)
- **GET** `/api/turfs/turfs/pending/` - Get pending turfs (admin only)
- **POST** `/api/turfs/turfs/{id}/upload_image/` - Upload turf image
- **GET** `/api/turfs/sports/` - List all sports
- **GET** `/api/turfs/amenities/` - List all amenities

### Reviews
- **GET** `/api/turfs/turfs/{turf_id}/reviews/` - List turf reviews
- **POST** `/api/turfs/turfs/{turf_id}/reviews/` - Create review

### Bookings
- **GET** `/api/bookings/bookings/` - List user's bookings
  - Query params: `status`, `date`, `turf_id`
- **POST** `/api/bookings/bookings/` - Create booking
- **GET** `/api/bookings/bookings/{id}/` - Get booking details
- **PUT/PATCH** `/api/bookings/bookings/{id}/` - Update booking
- **POST** `/api/bookings/bookings/{id}/confirm/` - Confirm booking
- **POST** `/api/bookings/bookings/{id}/cancel/` - Cancel booking
- **GET** `/api/bookings/bookings/my_bookings/` - Get categorized bookings (upcoming, completed, cancelled)
- **GET** `/api/bookings/bookings/availability/` - Check turf availability
  - Query params: `turf_id`, `booking_date`, `start_time`, `end_time`

## Core Utilities

### Google Maps Link Parser

Extract latitude and longitude from Google Maps share links:

```python
from core.utils import extract_coordinates_from_google_maps_share_link

result = extract_coordinates_from_google_maps_share_link('https://maps.app.goo.gl/xyz123')
# Returns: {
#     'success': True/False,
#     'latitude': float,
#     'longitude': float,
#     'message': str
# }
```

### Distance Calculation (Haversine Formula)

Calculate distance between two coordinates:

```python
from core.utils import calculate_distance_haversine

distance_km = calculate_distance_haversine(11.0083, 76.8666, 11.1271, 76.8900)
```

### Find Nearby Turfs

Find turfs within a radius:

```python
from core.utils import find_nearby_turfs

nearby = find_nearby_turfs(11.0083, 76.8666, turfs_list, radius_km=50)
# Returns: List of turfs sorted by distance
```

## Authentication

This API uses JWT (JSON Web Tokens) for authentication.

### Login Flow

1. Call login endpoint to get tokens:
```json
{
    "username": "user123",
    "password": "password123"
}
```

Response:
```json
{
    "success": true,
    "tokens": {
        "access": "eyJ0eXAiOiJKV1QiLCJhbGc...",
        "refresh": "eyJ0eXAiOiJKV1QiLCJhbGc..."
    }
}
```

2. Use `access` token in all requests:
```
Authorization: Bearer <access_token>
```

## Admin Dashboard

Access Django admin at: `http://127.0.0.1:8000/admin`

- Manage users and roles
- Approve/reject turf registrations
- View and manage bookings
- Monitor reviews and ratings
- Manage sports and amenities

## User Roles

### Normal User
- Browse approved turfs
- Search and filter turfs by location, price, amenities
- Book turfs
- View booking history
- Leave reviews

### Turf Owner
- Register turf by providing Google Maps share link
- Submit turf for approval (status: PENDING)
- Wait for admin approval
- Manage turf information
- Upload turf images
- View bookings for their turfs
- Check analytics and revenue

### Platform Admin
- View all pending turf registrations
- Approve or reject turf listings
- View all bookings and users
- Access full analytics
- Manage pricing and discount

## Database Models

### CustomUser
- username, email, password (inherited from Django User)
- role (user, turf_owner, admin)
- phone_number, profile_picture, bio
- is_verified

### TurfOwner
- user (OneToOne with CustomUser)
- bank_account, ifsc_code, gst_number
- total_turfs, total_bookings, total_revenue
- rating

### Turf
- owner (ForeignKey to CustomUser)
- name, description, address, city, state
- latitude, longitude (extracted from Google Maps)
- price_per_hour, max_players
- status (pending, approved, rejected, suspended)
- sports, amenities (ManyToMany)
- rating, review_count
- google_maps_share_link
- is_active

### TurfImage
- turf (ForeignKey)
- image, caption, is_cover
- uploaded_at

### Review
- turf (ForeignKey), user (ForeignKey)
- rating (1-5), comment
- created_at

### Booking
- user, turf (ForeignKey)
- booking_date, start_time, end_time
- price_per_hour, total_price, discount, final_price
- booking_status (pending, confirmed, completed, cancelled)
- payment_status (pending, paid, failed, refunded)
- notes, cancelled_reason, cancelled_at

### Payment
- booking (OneToOne)
- amount, payment_method, transaction_id
- status, created_at, paid_at

## Development Notes

- All API responses follow a consistent format with `success`, `message/error`, and `data` fields
- Pagination is enabled by default (20 items per page)
- CORS is enabled for all origins (restrict in production)
- Admin approval required for turfs to be visible to users
- Distance calculation uses Haversine formula for accuracy
- Google Maps link parsing supports standard formats and goo.gl shorteners

## Troubleshooting

### Google Maps Link Not Parsing
- Ensure the link is a valid Google Maps share link
- Try expanding the goo.gl shortener manually
- Provide link in format: `https://maps.app.goo.gl/{code}` or with `@lat,lng`

### Turf Not Appearing in User List
- Check turf status is "APPROVED"
- Verify turf's `is_active` is True
- Check user location and search radius
- Ensure turf has valid latitude and longitude

### Authentication Issues
- Ensure access token is included in request headers
- Token may have expired (use refresh token to get new access token)
- Check token format: `Authorization: Bearer <token>`

## Next Steps

1. Deploy to production (use PostgreSQL instead of SQLite)
2. Set up environment variables (.env file)
3. Configure payment gateway integration
4. Add email notifications
5. Set up celery for async tasks
6. Implement rating/review moderation
