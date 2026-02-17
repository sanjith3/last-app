"""
Views for turfs app.
"""

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from django.db.models import Q, Prefetch
from django.utils import timezone

from .models import Turf, TurfImage, Sport, Amenity, Review, TurfStatus
from .serializers import (
    TurfListSerializer,
    TurfDetailSerializer,
    TurfCreateUpdateSerializer,
    TurfRegistrationSerializer,
    TurfImageSerializer,
    SportSerializer,
    AmenitySerializer,
    ReviewSerializer,
    ReviewCreateSerializer,
)
from core.utils import calculate_distance_haversine, find_nearby_turfs


class SportViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet for sports."""
    queryset = Sport.objects.all()
    serializer_class = SportSerializer
    permission_classes = [AllowAny]


class AmenityViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet for amenities."""
    queryset = Amenity.objects.all()
    serializer_class = AmenitySerializer
    permission_classes = [AllowAny]


class TurfViewSet(viewsets.ModelViewSet):
    """ViewSet for turfs."""
    permission_classes = [IsAuthenticated]  # Default — overridden below for public actions
    lookup_field = 'pk'

    def get_permissions(self):
        """Public read, protected write."""
        if self.action in ['list', 'retrieve', 'reviews']:
            return [AllowAny()]
        return [IsAuthenticated()]
    
    def get_queryset(self):
        """Filter turfs based on user role and status."""
        user = self.request.user
        
        # Base queryset with necessary relations
        queryset = Turf.objects.select_related('owner').prefetch_related(
            'sports', 'amenities', 'images'
        ).distinct()
        
        if not user or user.is_anonymous:
            # Anonymous users see only approved and active turfs
            return queryset.filter(status=TurfStatus.APPROVED, is_active=True)
            
        if user.role == 'admin':
            # Admins see all turfs
            return queryset
            
        # Turf owners and normal users see:
        # 1. Any approved & active turf (for public browsing)
        # 2. ALSO any turf they own personally (for their dashboard)
        from django.db.models import Q
        return queryset.filter(
            Q(status=TurfStatus.APPROVED, is_active=True) |
            Q(owner=user)
        )
    
    def get_serializer_class(self):
        if self.action == 'create' or self.action == 'update' or self.action == 'partial_update':
            return TurfCreateUpdateSerializer
        elif self.action == 'register':
            return TurfRegistrationSerializer
        elif self.action == 'retrieve':
            return TurfDetailSerializer
        return TurfListSerializer
    
    def list(self, request, *args, **kwargs):
        """
        List turfs with optional filtering and distance calculation.
        Link: GET /api/turfs/turfs/?latitude=11.0083&longitude=76.8666&radius=50&search=query&city=Coimbatore
        """
        queryset = self.get_queryset()
        
        # Search filter
        search = request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(description__icontains=search) |
                Q(address__icontains=search)
            )
        
        # City filter
        city = request.query_params.get('city', '').strip()
        if city:
            queryset = queryset.filter(city__icontains=city)
        
        # My turfs filter (Dashboard mode)
        my_turfs = request.query_params.get('my_turfs', 'false').lower() == 'true'
        if my_turfs and request.user.is_authenticated:
            queryset = queryset.filter(owner=request.user)
        
        # Price filter
        min_price = request.query_params.get('min_price')
        max_price = request.query_params.get('max_price')
        if min_price:
            queryset = queryset.filter(price_per_hour__gte=int(min_price))
        if max_price:
            queryset = queryset.filter(price_per_hour__lte=int(max_price))
        
        # Nearby turfs with distance calculation
        latitude = request.query_params.get('latitude')
        longitude = request.query_params.get('longitude')
        
        turfs_data = []
        if latitude and longitude:
            try:
                user_lat = float(latitude)
                user_lon = float(longitude)
                radius = float(request.query_params.get('radius', 50))
                
                for turf in queryset:
                    distance = calculate_distance_haversine(user_lat, user_lon, turf.latitude, turf.longitude)
                    # Add distance to turf for serialization
                    turf.distance = distance
                    # Only include turfs within the radius
                    if distance <= radius:
                        turfs_data.append(turf)
                
                # Sort by distance
                turfs_data.sort(key=lambda t: t.distance)
            except (ValueError, TypeError):
                turfs_data = list(queryset)
        else:
            turfs_data = list(queryset)
        
        serializer = self.get_serializer(turfs_data, many=True)
        return Response({
            'success': True,
            'count': len(turfs_data),
            'results': serializer.data
        }, status=status.HTTP_200_OK)
    
    def retrieve(self, request, *args, **kwargs):
        """
        Get turf details with distance calculation if user location provided.
        Link: GET /api/turfs/turfs/{id}/?latitude=11.0083&longitude=76.8666
        """
        turf = self.get_object()
        
        # Calculate distance if user location provided
        latitude = request.query_params.get('latitude')
        longitude = request.query_params.get('longitude')
        if latitude and longitude:
            try:
                distance = calculate_distance_haversine(
                    float(latitude), float(longitude),
                    turf.latitude, turf.longitude
                )
                turf.distance = distance
            except (ValueError, TypeError):
                pass
        
        serializer = self.get_serializer(turf)
        return Response({
            'success': True,
            'data': serializer.data
        }, status=status.HTTP_200_OK)
    
    def create(self, request, *args, **kwargs):
        """Create a new turf (requires turf_owner role)."""
        if request.user.role != 'turf_owner':
            return Response({
                'success': False,
                'error': 'Only turf owners can create turfs'
            }, status=status.HTTP_403_FORBIDDEN)
        
        serializer = self.get_serializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            turf = serializer.save()
            return Response({
                'success': True,
                'message': 'Turf registered successfully and is pending approval',
                'data': TurfDetailSerializer(turf).data
            }, status=status.HTTP_201_CREATED)
        
        return Response({
            'success': False,
            'errors': serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)
    
    def update(self, request, *args, **kwargs):
        """Update turf (turf owner and admin only)."""
        turf = self.get_object()
        
        if request.user.role == 'turf_owner' and turf.owner != request.user:
            return Response({
                'success': False,
                'error': 'You can only update your own turfs'
            }, status=status.HTTP_403_FORBIDDEN)
        
        serializer = self.get_serializer(turf, data=request.data, context={'request': request}, partial=True)
        if serializer.is_valid():
            turf = serializer.save()
            return Response({
                'success': True,
                'message': 'Turf updated successfully',
                'data': TurfDetailSerializer(turf).data
            }, status=status.HTTP_200_OK)
        
        return Response({
            'success': False,
            'errors': serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Approve a turf (admin only)."""
        if request.user.role != 'admin':
            return Response({
                'success': False,
                'error': 'Only admins can approve turfs'
            }, status=status.HTTP_403_FORBIDDEN)
        
        turf = self.get_object()
        turf.approve()
        
        return Response({
            'success': True,
            'message': 'Turf approved successfully',
            'data': TurfDetailSerializer(turf).data
        }, status=status.HTTP_200_OK)
    
    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        """Reject a turf (admin only)."""
        if request.user.role != 'admin':
            return Response({
                'success': False,
                'error': 'Only admins can reject turfs'
            }, status=status.HTTP_403_FORBIDDEN)
        
        turf = self.get_object()
        reason = request.data.get('reason', 'Application rejected by platform admin')
        turf.reject(reason=reason)
        
        return Response({
            'success': True,
            'message': 'Turf rejected successfully',
            'data': TurfDetailSerializer(turf).data
        }, status=status.HTTP_200_OK)
    
    @action(detail=False, methods=['get'])
    def pending(self, request):
        """Get all pending turfs (admin only)."""
        if request.user.role != 'admin':
            return Response({
                'success': False,
                'error': 'Only admins can view pending turfs'
            }, status=status.HTTP_403_FORBIDDEN)
        
        turfs = Turf.objects.filter(status=TurfStatus.PENDING).select_related('owner').prefetch_related('sports', 'amenities', 'images')
        serializer = TurfListSerializer(turfs, many=True)
        
        return Response({
            'success': True,
            'count': turfs.count(),
            'results': serializer.data
        }, status=status.HTTP_200_OK)
    
    @action(detail=True, methods=['post'])
    def upload_image(self, request, pk=None):
        """Upload image for turf."""
        turf = self.get_object()
        
        if request.user.role == 'turf_owner' and turf.owner != request.user:
            return Response({
                'success': False,
                'error': 'You can only upload images for your own turfs'
            }, status=status.HTTP_403_FORBIDDEN)
        
        if 'image' not in request.FILES:
            return Response({
                'success': False,
                'error': 'No image provided'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        is_cover_raw = request.data.get('is_cover', False)
        # Handle string "true"/"false" from multipart/form-data
        if isinstance(is_cover_raw, str):
            is_cover = is_cover_raw.lower() == 'true'
        else:
            is_cover = bool(is_cover_raw)
        
        # If marking as cover, unmark previous cover
        if is_cover:
            turf.images.filter(is_cover=True).update(is_cover=False)
        
        image = TurfImage.objects.create(
            turf=turf,
            image=request.FILES['image'],
            is_cover=is_cover,
            caption=request.data.get('caption', '')
        )
        
        serializer = TurfImageSerializer(image)
        return Response({
            'success': True,
            'message': 'Image uploaded successfully',
            'data': serializer.data
        }, status=status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['get', 'post'])
    def reviews(self, request, pk=None):
        """Get or post reviews for a turf."""
        turf = self.get_object()
        
        if request.method == 'GET':
            reviews = turf.reviews.all().select_related('user')
            serializer = ReviewSerializer(reviews, many=True)
            return Response({
                'success': True,
                'count': reviews.count(),
                'results': serializer.data
            }, status=status.HTTP_200_OK)
        
        elif request.method == 'POST':
            # Check if user already reviewed this turf
            if Review.objects.filter(turf=turf, user=request.user).exists():
                return Response({
                    'success': False,
                    'error': 'You have already reviewed this turf'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            serializer = ReviewCreateSerializer(data=request.data)
            if serializer.is_valid():
                review = Review.objects.create(
                    turf=turf,
                    user=request.user,
                    rating=serializer.validated_data['rating'],
                    comment=serializer.validated_data['comment']
                )
                
                # Update turf rating
                reviews = turf.reviews.all()
                avg_rating = sum(r.rating for r in reviews) / reviews.count()
                turf.rating = avg_rating
                turf.review_count = reviews.count()
                turf.save()
                
                return Response({
                    'success': True,
                    'message': 'Review created successfully',
                    'data': ReviewSerializer(review).data
                }, status=status.HTTP_201_CREATED)
            
            return Response({
                'success': False,
                'errors': serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)
