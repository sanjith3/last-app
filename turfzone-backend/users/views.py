"""
Views for users app.
"""
import json
import logging
import uuid

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView
from django.contrib.auth import get_user_model
from django.db import transaction

logger = logging.getLogger('users.registration')

def _get_client_ip(request):
    """Extract client IP from request."""
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    return xff.split(',')[0].strip() if xff else request.META.get('REMOTE_ADDR', 'unknown')

# TODO: Remove after verification — temporary structured logging
REGISTRATION_AUDIT_ENABLED = True

from .serializers import (
    CustomUserRegistrationSerializer,
    CustomUserDetailSerializer,
    TurfOwnerRegistrationSerializer,
    LoginSerializer,
    ChangePasswordSerializer,
    TurfOwnerProfileSerializer,
)
from .models import TurfOwner, UserFavorite
from core.utils import extract_coordinates_from_google_maps_share_link
from turfs.models import Turf, TurfStatus, Sport, Amenity

User = get_user_model()


class UserRegistrationViewSet(viewsets.ViewSet):
    """ViewSet for user registration."""
    permission_classes = [AllowAny]
    
    @action(detail=False, methods=['post'])
    def normal_user_register(self, request):
        """Register a normal user."""
        serializer = CustomUserRegistrationSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            # Auto-verify: booking apps don't require manual admin approval
            if not user.is_verified:
                user.is_verified = True
                user.save(update_fields=['is_verified'])
            refresh = RefreshToken.for_user(user)
            return Response({
                'success': True,
                'message': 'User registered successfully',
                'user': CustomUserDetailSerializer(user).data,
                'tokens': {
                    'refresh': str(refresh),
                    'access': str(refresh.access_token),
                }
            }, status=status.HTTP_201_CREATED)
        return Response({
            'success': False,
            'errors': serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=False, methods=['post'])
    def turf_owner_register(self, request):
        """
        Register a turf owner with Google Maps share link.
        Supports multi-turf: existing owners can call this again to add more turfs.
        Link: POST /api/users/user-registration/turf_owner_register/
        """
        request_id = str(uuid.uuid4())[:8]
        phone = request.data.get('phone_number', '').strip()
        email = request.data.get('email', '').strip()
        
        serializer = TurfOwnerRegistrationSerializer(data=request.data)
        if serializer.is_valid():
            try:
                result = serializer.save()
                user = result['user']
                google_maps_link = result['google_maps_link']
                is_existing = result.get('is_existing_owner', False)
                
                # Structured audit log
                if REGISTRATION_AUDIT_ENABLED:
                    logger.info('[REGISTRATION_AUDIT] %s', json.dumps({
                        'request_id': request_id,
                        'endpoint': 'turf_owner_register',
                        'phone': phone,
                        'email': email,
                        'existing_user_id': user.id if is_existing else None,
                        'branch_taken': 'reuse_existing' if is_existing else 'create_new',
                        'ip_address': _get_client_ip(request),
                        'user_id': user.id,
                        'user_role': user.role,
                    }))
                
                # Extract coordinates from Google Maps link
                coords = extract_coordinates_from_google_maps_share_link(google_maps_link)
                
                if coords['success']:
                    # Create turf as PENDING (works for both new and existing owners)
                    turf = Turf.objects.create(
                        owner=user,
                        name=request.data.get('turf_name', 'New Turf'),
                        description=request.data.get('description', ''),
                        address=request.data.get('address', ''),
                        city=request.data.get('city', ''),
                        state=request.data.get('state', ''),
                        latitude=coords['latitude'],
                        longitude=coords['longitude'],
                        price_per_hour=request.data.get('price_per_hour', 500),
                        status=TurfStatus.PENDING,
                        is_active=False,
                        google_maps_share_link=google_maps_link,
                    )
                    
                    # Auto-verify account (turf still needs separate approval)
                    if not user.is_verified:
                        user.is_verified = True
                        user.save(update_fields=['is_verified'])
                    refresh = RefreshToken.for_user(user)
                    message = (
                        'New turf added successfully. Your turf is pending approval.'
                        if is_existing
                        else 'Turf owner registered successfully. Your turf is pending approval.'
                    )
                    return Response({
                        'success': True,
                        'message': message,
                        'user': CustomUserDetailSerializer(user).data,
                        'turf_id': turf.id,
                        'total_turfs': user.turfs.count(),
                        'tokens': {
                            'refresh': str(refresh),
                            'access': str(refresh.access_token),
                        }
                    }, status=status.HTTP_201_CREATED)
                else:
                    # Only delete user if they were just created (not existing)
                    if not is_existing:
                        user.delete()
                    return Response({
                        'success': False,
                        'error': coords['message'],
                        'error_code': 'could_not_extract_coordinates',
                        'debug_id': coords.get('debug_id'),
                    }, status=status.HTTP_400_BAD_REQUEST)
                    
            except Exception as e:
                return Response({
                    'success': False,
                    'error': str(e)
                }, status=status.HTTP_400_BAD_REQUEST)
        
        return Response({
            'success': False,
            'errors': serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)


class UserLoginViewSet(viewsets.ViewSet):
    """ViewSet for user login."""
    permission_classes = [AllowAny]
    authentication_classes = []
    
    @action(detail=False, methods=['post'])
    def login(self, request):
        """
        Login user with username and password.
        Link: POST /api/users/user-login/login/
        """
        print(f"DEBUG: Login attempt for: {request.data.get('username')}")
        serializer = LoginSerializer(data=request.data)
        if serializer.is_valid():
            try:
                username_or_phone = serializer.validated_data['username']
                # Try by username
                user = User.objects.filter(username=username_or_phone).first()
                if not user:
                    # Try by phone_number
                    user = User.objects.filter(phone_number=username_or_phone).first()
                if not user:
                    # Try by email
                    user = User.objects.filter(email=username_or_phone).first()

                if user:
                    if user.check_password(serializer.validated_data['password']):
                        refresh = RefreshToken.for_user(user)
                        print(f"DEBUG: Login successful for: {user.username}")
                        return Response({
                            'success': True,
                            'message': 'Login successful',
                            'user': CustomUserDetailSerializer(user).data,
                            'tokens': {
                                'refresh': str(refresh),
                                'access': str(refresh.access_token),
                            }
                        }, status=status.HTTP_200_OK)
                    else:
                        print(f"DEBUG: Password check failed for: {user.username}")
                else:
                    print(f"DEBUG: User not found: {username_or_phone}")

                return Response({
                    'success': False,
                    'error': 'Invalid credentials'
                }, status=status.HTTP_401_UNAUTHORIZED)
            except Exception as e:
                print(f"DEBUG: Login error: {str(e)}")
                return Response({
                    'success': False,
                    'error': 'An error occurred during login'
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        print(f"DEBUG: Serializer invalid: {serializer.errors}")
        return Response({
            'success': False,
            'errors': serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)


class UserProfileViewSet(viewsets.ViewSet):
    """ViewSet for user profile management."""
    permission_classes = [IsAuthenticated]
    
    @action(detail=False, methods=['get'])
    def me(self, request):
        """
        Get current user profile.
        Link: GET /api/users/user-profile/me/
        """
        user = request.user
        data = CustomUserDetailSerializer(user).data
        
        # Add turf owner data if applicable
        if user.role == 'turf_owner':
            try:
                turf_owner = TurfOwner.objects.get(user=user)
                data['turf_owner'] = TurfOwnerProfileSerializer(turf_owner).data
            except TurfOwner.DoesNotExist:
                pass
        
        return Response({
            'success': True,
            'user': data
        }, status=status.HTTP_200_OK)
    
    @action(detail=False, methods=['put', 'patch'])
    def update_profile(self, request):
        """
        Update user profile.
        Link: PUT/PATCH /api/users/user-profile/update_profile/
        """
        user = request.user
        serializer = CustomUserDetailSerializer(user, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response({
                'success': True,
                'message': 'Profile updated successfully',
                'user': serializer.data
            }, status=status.HTTP_200_OK)
        
        return Response({
            'success': False,
            'errors': serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=False, methods=['post'])
    def become_partner(self, request):
        """
        Upgrade current user to turf owner and register a turf.
        Can be called multiple times to add more turfs.
        Link: POST /api/users/user-profile/become_partner/
        """
        user = request.user
        data = request.data
        request_id = str(uuid.uuid4())[:8]
        
        try:
            with transaction.atomic():
                # 1. Upgrade user role to turf_owner (idempotent)
                if user.role != 'turf_owner' and user.role != 'admin':
                    user.role = 'turf_owner'
                    user.save(update_fields=['role'])
                
                # 2. Get or create TurfOwner profile
                turf_owner, created = TurfOwner.objects.get_or_create(user=user)
                if data.get('bank_account'):
                    turf_owner.bank_account = data.get('bank_account')
                if data.get('ifsc_code'):
                    turf_owner.ifsc_code = data.get('ifsc_code')
                if data.get('bank_name'):
                    turf_owner.bank_name = data.get('bank_name')
                if data.get('account_holder_name'):
                    turf_owner.account_holder_name = data.get('account_holder_name')
                turf_owner.save()
                
                # 3. Extract coordinates from Google Maps link
                google_maps_link = data.get('google_maps_share_link')
                if not google_maps_link:
                    return Response({'success': False, 'error': 'Google Maps share link is required'}, status=status.HTTP_400_BAD_REQUEST)
                
                coords = extract_coordinates_from_google_maps_share_link(google_maps_link)
                if not coords['success']:
                    return Response({'success': False, 'error': coords['message']}, status=status.HTTP_400_BAD_REQUEST)
                
                # 4. Create the Turf in PENDING status
                turf = Turf.objects.create(
                    owner=user,
                    name=data.get('turf_name', 'New Turf'),
                    description=data.get('description', ''),
                    address=data.get('address', ''),
                    city=data.get('city', ''),
                    state=data.get('state', ''),
                    postal_code=data.get('postal_code', ''),
                    latitude=coords['latitude'],
                    longitude=coords['longitude'],
                    price_per_hour=data.get('price_per_hour', 500),
                    status=TurfStatus.PENDING,
                    is_active=False,
                    google_maps_share_link=google_maps_link,
                )
                
                # 5. Add Sports
                sports_list = data.get('sports', [])
                for sport_name in sports_list:
                    sport, _ = Sport.objects.get_or_create(name=sport_name)
                    turf.sports.add(sport)
                
                # 6. Add Amenities
                amenities_list = data.get('amenities', [])
                for amenity_name in amenities_list:
                    amenity, _ = Amenity.objects.get_or_create(name=amenity_name)
                    turf.amenities.add(amenity)
                
                total_turfs = user.turfs.count()
                is_first_turf = total_turfs == 1
                message = (
                    'Successfully applied to become a partner. Your turf is pending approval.'
                    if is_first_turf
                    else f'New turf added successfully. You now have {total_turfs} turfs.'
                )
                
                # Structured audit log
                if REGISTRATION_AUDIT_ENABLED:
                    logger.info('[REGISTRATION_AUDIT] %s', json.dumps({
                        'request_id': request_id,
                        'endpoint': 'become_partner',
                        'phone': user.phone_number or '',
                        'email': user.email or '',
                        'existing_user_id': user.id,
                        'branch_taken': 'first_turf' if is_first_turf else 'additional_turf',
                        'ip_address': _get_client_ip(request),
                        'user_id': user.id,
                        'user_role': user.role,
                        'turf_id': turf.id,
                        'total_turfs': total_turfs,
                    }))
                
                return Response({
                    'success': True,
                    'message': message,
                    'turf_id': turf.id,
                    'total_turfs': user.turfs.count(),
                    'user': CustomUserDetailSerializer(user).data
                }, status=status.HTTP_201_CREATED)
                
        except Exception as e:
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'])
    def change_password(self, request):
        """
        Change user password.
        Link: POST /api/users/user-profile/change_password/
        """
        user = request.user
        serializer = ChangePasswordSerializer(data=request.data)
        
        if serializer.is_valid():
            if not user.check_password(serializer.validated_data['old_password']):
                return Response({
                    'success': False,
                    'error': 'Old password is incorrect'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            user.set_password(serializer.validated_data['new_password'])
            user.save()
            
            return Response({
                'success': True,
                'message': 'Password changed successfully'
            }, status=status.HTTP_200_OK)
        
        return Response({
            'success': False,
            'errors': serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)

    # -----------------------------------------------------------------------
    # Favorites
    # -----------------------------------------------------------------------

    @action(detail=False, methods=['post'], url_path='favorites/toggle')
    def toggle_favorite(self, request):
        """
        Toggle a turf as favorite.
        POST /api/users/user-profile/favorites/toggle/
        Body: { "turf_id": 7 }
        """
        turf_id = request.data.get('turf_id')
        if not turf_id:
            return Response({'success': False, 'error': 'turf_id is required'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            turf = Turf.objects.get(id=turf_id, status=TurfStatus.APPROVED)
        except Turf.DoesNotExist:
            return Response({'success': False, 'error': 'Turf not found'},
                            status=status.HTTP_404_NOT_FOUND)

        fav, created = UserFavorite.objects.get_or_create(
            user=request.user, turf=turf,
        )
        if not created:
            fav.delete()

        return Response({
            'success': True,
            'is_favorite': created,
        })

    @action(detail=False, methods=['get'], url_path='favorites')
    def list_favorites(self, request):
        """
        List all favorited turfs for the current user.
        GET /api/users/user-profile/favorites/
        Returns full turf objects.
        """
        from turfs.serializers import TurfListSerializer

        fav_turf_ids = UserFavorite.objects.filter(
            user=request.user,
        ).values_list('turf_id', flat=True)

        turfs = Turf.objects.filter(
            id__in=fav_turf_ids,
            status=TurfStatus.APPROVED,
        ).prefetch_related('sports', 'amenities', 'images')

        serializer = TurfListSerializer(
            turfs, many=True,
            context={'request': request},
        )
        return Response({
            'success': True,
            'count': turfs.count(),
            'results': serializer.data,
        })

    @action(detail=False, methods=['post', 'get'], url_path='favorites/check')
    def check_favorites(self, request):
        """
        Check favorite status.
        GET  /api/users/user-profile/favorites/check/?turf_id=7
        POST /api/users/user-profile/favorites/check/   body: { "turf_ids": [1,2,3] }
        """
        if request.method == 'GET':
            turf_id = request.query_params.get('turf_id')
            if not turf_id:
                return Response({'success': False, 'error': 'turf_id is required'},
                                status=status.HTTP_400_BAD_REQUEST)
            is_fav = UserFavorite.objects.filter(
                user=request.user, turf_id=turf_id,
            ).exists()
            return Response({'success': True, 'is_favorite': is_fav})

        # POST — bulk check
        turf_ids = request.data.get('turf_ids', [])
        if not turf_ids:
            return Response({'success': True, 'favorites': {}})

        fav_set = set(
            UserFavorite.objects.filter(
                user=request.user, turf_id__in=turf_ids,
            ).values_list('turf_id', flat=True)
        )
        favorites = {str(tid): (tid in fav_set) for tid in turf_ids}
        return Response({'success': True, 'favorites': favorites})


class TurfOwnerProfileViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet for viewing turf owner profiles."""
    queryset = TurfOwner.objects.all()
    serializer_class = TurfOwnerProfileSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'user_id'
