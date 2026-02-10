"""
Views for users app.
"""

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView
from django.contrib.auth import get_user_model
from django.db import transaction

from .serializers import (
    CustomUserRegistrationSerializer,
    CustomUserDetailSerializer,
    TurfOwnerRegistrationSerializer,
    LoginSerializer,
    ChangePasswordSerializer,
    TurfOwnerProfileSerializer,
)
from .models import TurfOwner
from core.utils import extract_coordinates_from_google_maps_share_link
from turfs.models import Turf, TurfStatus

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
        Link: POST /api/users/user-registration/turf_owner_register/
        """
        serializer = TurfOwnerRegistrationSerializer(data=request.data)
        if serializer.is_valid():
            try:
                result = serializer.save()
                user = result['user']
                google_maps_link = result['google_maps_link']
                
                # Extract coordinates from Google Maps link
                coords = extract_coordinates_from_google_maps_share_link(google_maps_link)
                
                if coords['success']:
                    # Create initial turf as PENDING
                    turf = Turf.objects.create(
                        owner=user,
                        name=request.data.get('turf_name', 'New Turf'),
                        description='Pending description',
                        address=request.data.get('address', ''),
                        city=request.data.get('city', ''),
                        state=request.data.get('state', ''),
                        latitude=coords['latitude'],
                        longitude=coords['longitude'],
                        price_per_hour=request.data.get('price_per_hour', 500),
                        status=TurfStatus.PENDING,
                        google_maps_share_link=google_maps_link,
                    )
                    
                    refresh = RefreshToken.for_user(user)
                    return Response({
                        'success': True,
                        'message': 'Turf owner registered successfully. Your turf is pending approval.',
                        'user': CustomUserDetailSerializer(user).data,
                        'turf_id': turf.id,
                        'tokens': {
                            'refresh': str(refresh),
                            'access': str(refresh.access_token),
                        }
                    }, status=status.HTTP_201_CREATED)
                else:
                    # Delete user if coordinates couldn't be extracted
                    user.delete()
                    return Response({
                        'success': False,
                        'error': coords['message']
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
    
    @action(detail=False, methods=['post'])
    def login(self, request):
        """
        Login user with username and password.
        Link: POST /api/users/user-login/login/
        """
        serializer = LoginSerializer(data=request.data)
        if serializer.is_valid():
            try:
                user = User.objects.get(username=serializer.validated_data['username'])
                if user.check_password(serializer.validated_data['password']):
                    refresh = RefreshToken.for_user(user)
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
                    return Response({
                        'success': False,
                        'error': 'Invalid credentials'
                    }, status=status.HTTP_401_UNAUTHORIZED)
            except User.DoesNotExist:
                return Response({
                    'success': False,
                    'error': 'Invalid credentials'
                }, status=status.HTTP_401_UNAUTHORIZED)
        
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


class TurfOwnerProfileViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet for viewing turf owner profiles."""
    queryset = TurfOwner.objects.all()
    serializer_class = TurfOwnerProfileSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'user_id'
