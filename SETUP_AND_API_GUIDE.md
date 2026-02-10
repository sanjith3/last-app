# TurfZone - Full Stack Application Setup Guide

Complete setup and documentation for the TurfZone turf booking application with Django backend and Flutter mobile frontend.

## 📋 Project Overview

**TurfZone** is a comprehensive turf booking platform consisting of:

- **Django REST Backend**: Full-featured backend with user authentication, turf management, bookings, and admin approval system
- **Flutter Mobile App**: Cross-platform mobile app for iOS and Android users to browse and book turfs

### Key Features ✨

✅ User authentication (Normal User, Turf Owner, Platform Admin)
✅ Google Maps integration for turf location
✅ Automatic coordinate extraction from Google Maps share links
✅ Distance calculation using Haversine formula
✅ Turf registration with admin approval workflow
✅ Real-time booking management
✅ Review and rating system
✅ Responsive mobile UI
✅ JWT token-based authentication

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.10+** (for Django backend)
- **Flutter 3.10+** (for mobile app)
- **Git**
- **PostgreSQL** (optional, for production)
- **Android Studio / Xcode** (for mobile development)

### Backend Setup (Django)

#### 1. Clone and Navigate

```bash
cd d:\final-app\turfzone-backend
```

#### 2. Create Virtual Environment

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS/Linux
python3 -m venv venv
source venv/bin/activate
```

#### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

#### 4. Database Setup

```bash
# Apply migrations
python manage.py makemigrations
python manage.py migrate

# Create superuser (admin account)
python manage.py createsuperuser
# Follow prompts: username, email, password
```

#### 5. Create Initial Data (Sports & Amenities)

```bash
python manage.py shell
```

In the Django shell, run:

```python
from turfs.models import Sport, Amenity

# Create Sports
sports = [
    'Cricket', 'Football', 'Badminton', 'Tennis', 
    'Basketball', 'Volleyball', 'Kabaddi'
]
for sport_name in sports:
    Sport.objects.get_or_create(name=sport_name)

# Create Amenities
amenities = [
    'Flood Lights', 'Parking', 'Water', 'WiFi', 'Cafeteria',
    'Changing Rooms', 'Showers', 'Lockers', 'First Aid', 'CCTV',
    'Equipment Rental', 'Seating Area', 'Gym'
]
for amenity_name in amenities:
    Amenity.objects.get_or_create(name=amenity_name)

exit()
```

#### 6. Run Development Server

```bash
python manage.py runserver
```

Server runs at: `http://127.0.0.1:8000`

Access Django Admin: `http://127.0.0.1:8000/admin`

---

### Flutter Setup (Mobile App)

#### 1. Navigate to Flutter App

```bash
cd d:\final-app\turfzone-app
```

#### 2. Get Dependencies

```bash
flutter pub get
```

#### 3. Update API Base URL

Edit `lib/services/api_service.dart`:

```dart
static const String BASE_URL = 'http://YOUR_BACKEND_URL:8000/api';
```

Replace `YOUR_BACKEND_URL` with your Django server IP/domain.

#### 4. Run App

```bash
# Android
flutter run -d android

# iOS
flutter run -d ios

# Web (if enabled)
flutter run -d chrome
```

---

## 📱 Architecture Overview

### Django Backend Structure

```
turfzone-backend/
├── turfzone/              # Main settings
├── core/                  # Shared utilities (Google Maps parser, Haversine)
├── users/                 # Authentication & user management
├── turfs/                 # Turf listing & approval system
├── bookings/              # Booking management
├── manage.py
└── requirements.txt
```

### Flutter App Structure

```
turfzone-app/
├── lib/
│   ├── main.dart          # Entry point
│   ├── models/            # Data models
│   ├── services/
│   │   ├── api_service.dart    # API client (NEW)
│   │   ├── turf_data_service.dart  # Local cache (CLEANED)
│   │   └── offer_slot_service.dart
│   ├── features/          # Feature screens
│   └── booking/
├── pubspec.yaml           # Dependencies
└── assets/                # Images & resources
```

---

## 🔐 Authentication Flow

### User Registration (Normal User)

```
POST /api/users/user-registration/normal_user_register/

Body:
{
    "username": "john_doe",
    "email": "john@example.com",
    "password": "password123",
    "first_name": "John",
    "last_name": "Doe",
    "phone_number": "+91-9876543210",
    "role": "user"
}

Response:
{
    "success": true,
    "tokens": {
        "access": "eyJ0eXAiOiJKV1Q...",
        "refresh": "eyJ0eXAiOiJKV1Q..."
    }
}
```

### Turf Owner Registration (with Google Maps Link)

```
POST /api/users/user-registration/turf_owner_register/

Body:
{
    "username": "owner123",
    "email": "owner@example.com",
    "password": "password123",
    "password_confirm": "password123",
    "first_name": "Turf",
    "last_name": "Owner",
    "phone_number": "+91-9876543210",
    "google_maps_share_link": "https://maps.app.goo.gl/abc123",
    "turf_name": "Elite Sports Ground",
    "address": "123 Sports Park Road",
    "city": "Coimbatore",
    "state": "Tamil Nadu",
    "price_per_hour": 500
}

Response:
{
    "success": true,
    "message": "Turf owner registered successfully. Turf is pending approval.",
    "turf_id": 1,
    "tokens": {...}
}
```

### Login

```
POST /api/users/user-login/login/

Body:
{
    "username": "john_doe",
    "password": "password123"
}

Response:
{
    "success": true,
    "tokens": {
        "access": "...",
        "refresh": "..."
    }
}
```

---

## 🎯 API Endpoints Reference

### Authentication Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/users/user-registration/normal_user_register/` | Register normal user |
| POST | `/api/users/user-registration/turf_owner_register/` | Register turf owner |
| POST | `/api/users/user-login/login/` | User login |
| GET | `/api/users/user-profile/me/` | Get current user profile |
| PUT | `/api/users/user-profile/update_profile/` | Update user profile |
| POST | `/api/users/user-profile/change_password/` | Change password |

### Turf Endpoints

| Method | Endpoint | Purpose | Auth |
|--------|----------|---------|------|
| GET | `/api/turfs/turfs/` | List turfs (with optional filters) | ✓ |
| POST | `/api/turfs/turfs/` | Create new turf | ✓ (Turf Owner) |
| GET | `/api/turfs/turfs/{id}/` | Get turf details | ✓ |
| PUT | `/api/turfs/turfs/{id}/` | Update turf | ✓ (Owner/Admin) |
| POST | `/api/turfs/turfs/{id}/approve/` | Approve turf | ✓ (Admin) |
| POST | `/api/turfs/turfs/{id}/reject/` | Reject turf | ✓ (Admin) |
| GET | `/api/turfs/turfs/pending/` | Get pending turfs | ✓ (Admin) |
| POST | `/api/turfs/turfs/{id}/upload_image/` | Upload turf image | ✓ |
| GET | `/api/turfs/turfs/{id}/reviews/` | Get turf reviews | ✓ |
| POST | `/api/turfs/turfs/{id}/reviews/` | Post turf review | ✓ |
| GET | `/api/turfs/sports/` | List sports | ✓ |
| GET | `/api/turfs/amenities/` | List amenities | ✓ |

### Booking Endpoints

| Method | Endpoint | Purpose | Auth |
|--------|----------|---------|------|
| GET | `/api/bookings/bookings/` | List user bookings | ✓ |
| POST | `/api/bookings/bookings/` | Create booking | ✓ |
| GET | `/api/bookings/bookings/{id}/` | Get booking details | ✓ |
| PUT | `/api/bookings/bookings/{id}/` | Update booking | ✓ |
| POST | `/api/bookings/bookings/{id}/confirm/` | Confirm booking | ✓ |
| POST | `/api/bookings/bookings/{id}/cancel/` | Cancel booking | ✓ |
| GET | `/api/bookings/bookings/my_bookings/` | Get categorized bookings | ✓ |
| GET | `/api/bookings/bookings/availability/` | Check availability | ✓ |

---

## 🗺️ Google Maps Integration

### Supported Link Formats

The backend automatically parses and extracts coordinates from:

1. **Google Maps Share Link (Short)**
   ```
   https://maps.app.goo.gl/abc123XYZ
   ```

2. **Google Maps URL with Coordinates**
   ```
   https://www.google.com/maps/place/11.0083,76.8666
   https://www.google.com/maps/@11.0083,76.8666,15z
   ```

### Direction Button Integration

When users tap "Direction" on a turf detail:

```dart
final directionsUrl = 'https://www.google.com/maps/dir/?api=1&destination=${turf.latitude},${turf.longitude}';
await launchUrl(Uri.parse(directionsUrl));
```

This opens Google Maps navigation directly.

---

## 📊 Distance Calculation

The backend uses **Haversine formula** to calculate real distances between user location and turfs.

### API Usage

```
GET /api/turfs/turfs/?latitude=11.0083&longitude=76.8666&radius=50
```

**Query Parameters:**
- `latitude` (float): User's latitude
- `longitude` (float): User's longitude
- `radius` (float, optional): Search radius in km (default: 50)

**Response includes `distance` field for each turf (in km)**

---

## 👤 User Roles & Permissions

### Normal User
- Browse approved turfs
- Search & filter turfs
- View turf details
- Book turfs
- View booking history
- Leave reviews
- Cancel bookings

### Turf Owner
- Register turf (with Google Maps link)
- Upload turf images
- Manage turf information
- View bookings for their turfs
- View analytics
- Access pending approval status

### Platform Admin
- View all pending turf registrations
- Approve/reject turfs
- View all bookings system-wide
- View all users
- Access analytics & reports
- Manage sports & amenities

---

## 🔧 Configuration

### Django Settings (Important)

Edit `turfzone/settings.py`:

```python
# Database (Production)
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'turfzone',
        'USER': 'postgres',
        'PASSWORD': 'password',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}

# CORS (Production - restrict origins)
CORS_ALLOWED_ORIGINS = [
    "https://yourdomain.com",
    "https://app.yourdomain.com",
]

# JWT Settings
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(days=7),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=30),
}

# Email Configuration (for notifications)
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'your-email@gmail.com'
EMAIL_HOST_PASSWORD = 'your-app-password'
```

### Flutter Configuration

Edit `lib/services/api_service.dart`:

```dart
// Development
static const String BASE_URL = 'http://192.168.1.100:8000/api';  // Local IP

// Production
static const String BASE_URL = 'https://api.turfzone.com/api';   // Domain
```

---

## 📦 Deployment

### Backend Deployment (Django)

#### Using Heroku

```bash
# Install Heroku CLI
# Create Procfile
echo "web: gunicorn turfzone.wsgi" > Procfile

# Create runtime.txt
echo "python-3.10.12" > runtime.txt

# Deploy
heroku create turfzone-api
git push heroku main
heroku run python manage.py migrate
heroku run python manage.py createsuperuser
```

#### Using AWS/DigitalOcean

1. Set up VPS with Python, PostgreSQL, Nginx
2. Clone repository
3. Set up virtual environment
4. Configure environment variables (.env)
5. Run migrations
6. Start Gunicorn with Nginx proxy

### Flutter Deployment

#### Android (Google Play Store)

```bash
# Build release APK
flutter build apk --release

# Build app bundle (for Play Store)
flutter build appbundle --release
```

#### iOS (App Store)

```bash
# Build release IPA
flutter build ios --release
```

---

## 🐛 Troubleshooting

### "Connection refused" Error

**Problem**: Flutter app can't connect to Django backend

**Solution**:
1. Check if Django server is running: `python manage.py runserver`
2. Update API URL in `api_service.dart` to match your server IP
3. For Android emulator: use `10.0.2.2:8000` instead of `localhost:8000`
4. Check firewall rules

### Google Maps Link Not Parsing

**Problem**: "Could not extract coordinates from link"

**Solution**:
1. Ensure link is a valid Google Maps share link
2. Try: `https://maps.app.goo.gl/{shortcode}`
3. Test parsing with: `python manage.py shell`
   ```python
   from core.utils import extract_coordinates_from_google_maps_share_link
   result = extract_coordinates_from_google_maps_share_link('your_link')
   print(result)
   ```

### Turf Not Appearing in User List

**Problem**: Created turf not visible to users

**Solution**:
1. Check turf status = "APPROVED" (check Django admin)
2. Verify `is_active` = True
3. Use correct user location in API query (latitude/longitude)
4. Check turf has valid coordinates

### Authentication Token Expired

**Problem**: "401 Unauthorized" errors

**Solution**:
1. Token expires after 7 days by default
2. Implement token refresh in Flutter:
   ```dart
   // Use refresh_token to get new access_token
   POST /api/token/refresh/
   ```

---

## 📚 Additional Resources

### API Documentation
- Full API docs: [Backend README](turfzone-backend/README.md)

### Code References
- Django Models: `turfzone-backend/*/models.py`
- Serializers: `turfzone-backend/*/serializers.py`
- Views: `turfzone-backend/*/views.py`
- Utilities: `turfzone-backend/core/utils.py`
- Flutter Services: `turfzone-app/lib/services/`

### External Links
- [Django REST Framework Docs](https://www.django-rest-framework.org/)
- [Flutter Documentation](https://flutter.dev/docs)
- [Django Admin Customization](https://docs.djangoproject.com/en/4.2/ref/contrib/admin/)

---

## 📝 Project Documentation Checklist

✅ Backend Django project created
✅ All models (User, Turf, Booking, etc.) implemented
✅ DRF serializers and views created
✅ Google Maps coordinate extraction
✅ Haversine distance calculation
✅ JWT authentication
✅ Admin approval system
✅ Flutter API client created
✅ Mock data removed from Flutter
✅ API endpoints documented
✅ Comprehensive setup guide
✅ Troubleshooting guide
✅ Deployment instructions

---

## 🎓 Architecture Highlights

### Separation of Concerns
- **Flutter**: UI rendering + API calls only
- **Django**: Business logic + Database + API

### Google Maps Integration
- Automatic coordinate extraction from share links
- No embedded maps required
- Direct navigation URL generation

### Distance Calculation
- Haversine formula for accurate distances
- Sorts results by proximity
- Configurable search radius

### Admin Approval Workflow
- Turfs created with status = PENDING
- Admin approves/rejects in dashboard
- Only approved turfs visible to users
- Full audit trail

### Security
- JWT token-based authentication
- Role-based access control (User, Turf Owner, Admin)
- Password hashing with Django's built-in system
- CORS protection

---

## 📞 Support

For issues or questions:
1. Check troubleshooting section above
2. Review Django logs: `tail -f logs/django.log`
3. Check Flutter console for errors
4. Review API responses in browser DevTools

---

**Last Updated**: February 2026
**Status**: ✅ Production Ready
