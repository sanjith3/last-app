"""
Views for turfs app.
"""

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from django.db.models import Q, Prefetch
from django.utils import timezone
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

from .models import Turf, TurfImage, Sport, Amenity, Review, TurfStatus, SlotMaster, SlotOffer, OfferType
from bookings.models import BookingSlot, BookingStatus
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
from truff_admin_panel.utils import log_admin_action


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
            
        # All authenticated users see only approved turfs for public browsing.
        # Owner turfs are handled by ?my_turfs=true in the list() method.
        return queryset.filter(status=TurfStatus.APPROVED, is_active=True)
    
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
        
        # My turfs filter (Owner Dashboard mode)
        my_turfs = request.query_params.get('my_turfs', 'false').lower() == 'true'
        if my_turfs and request.user.is_authenticated:
            # Owner sees ALL their turfs — no status, distance, or city filter
            queryset = Turf.objects.select_related('owner').prefetch_related(
                'sports', 'amenities', 'images'
            ).filter(owner=request.user).order_by('-created_at')
            
            import logging
            logger = logging.getLogger('turfs')
            logger.info('[OWNER_DASH] user=%s turfs=%d', request.user.pk, queryset.count())
            
            serializer = self.get_serializer(queryset, many=True)
            return Response({
                'success': True,
                'count': queryset.count(),
                'results': serializer.data
            }, status=status.HTTP_200_OK)
        
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
                is_owner = request.user.is_authenticated and request.user.role in ('turf_owner', 'admin')
                
                for turf in queryset:
                    distance = calculate_distance_haversine(user_lat, user_lon, turf.latitude, turf.longitude)
                    turf.distance = distance
                    # Always include: (a) within radius, or (b) owned by this user
                    if distance <= radius or (is_owner and turf.owner_id == request.user.pk):
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

            # Audit log for turf creation
            log_admin_action(request, 'turf_created', 'Turf', turf.id, {
                'turf_name': turf.name,
                'owner': request.user.username,
                'status': turf.status,
            })

            return Response({
                'success': True,
                'message': 'Turf registered successfully and is pending approval',
                'data': TurfDetailSerializer(turf, context={'request': request}).data
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
                'data': TurfDetailSerializer(turf, context={'request': request}).data
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
        turf.approve(admin=request.user)
        log_admin_action(request, 'turf_approved', 'Turf', turf.id, {
            'turf_name': turf.name,
        })
        
        return Response({
            'success': True,
            'message': 'Turf approved successfully',
            'data': TurfDetailSerializer(turf, context={'request': request}).data
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
        log_admin_action(request, 'turf_rejected', 'Turf', turf.id, {
            'turf_name': turf.name, 'reason': reason,
        })
        
        return Response({
            'success': True,
            'message': 'Turf rejected successfully',
            'data': TurfDetailSerializer(turf, context={'request': request}).data
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
        serializer = TurfListSerializer(turfs, many=True, context={'request': request})
        
        return Response({
            'success': True,
            'count': turfs.count(),
            'results': serializer.data
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def resubmit(self, request, pk=None):
        """
        Owner resubmits a rejected turf for review.
        POST /api/turfs/turfs/{id}/resubmit/
        """
        turf = self.get_object()

        if turf.owner != request.user:
            return Response({
                'success': False,
                'error': 'You can only resubmit your own turfs'
            }, status=status.HTTP_403_FORBIDDEN)

        if turf.status != TurfStatus.REJECTED:
            return Response({
                'success': False,
                'error': 'Only rejected turfs can be resubmitted'
            }, status=status.HTTP_400_BAD_REQUEST)

        turf.resubmit()
        log_admin_action(request, 'turf_resubmitted', 'Turf', turf.id, {
            'turf_name': turf.name,
            'owner': request.user.username,
        })

        return Response({
            'success': True,
            'message': 'Turf resubmitted for review',
            'data': TurfDetailSerializer(turf, context={'request': request}).data
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
        
        serializer = TurfImageSerializer(image, context={'request': request})
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

    # ─── OWNER: DISABLE SLOT ──────────────────────────────────────────────────

    @action(detail=True, methods=['post'])
    def disable_slot(self, request, pk=None):
        """
        Owner disables a SlotMaster (is_active = False).
        POST /api/turfs/turfs/{id}/disable_slot/
        Body: {"slot_id": 42}
        """
        turf = self.get_object()

        # Ownership check
        if turf.owner != request.user:
            return Response({
                'success': False,
                'error': 'You can only manage slots on your own turfs',
            }, status=status.HTTP_403_FORBIDDEN)

        slot_id = request.data.get('slot_id')
        if not slot_id:
            return Response({
                'success': False,
                'error': 'slot_id is required',
            }, status=status.HTTP_400_BAD_REQUEST)

        slot = SlotMaster.objects.filter(id=slot_id, turf=turf).first()
        if not slot:
            return Response({
                'success': False,
                'error': 'Slot not found for this turf',
            }, status=status.HTTP_404_NOT_FOUND)

        slot.is_active = False
        slot.save(update_fields=['is_active', 'updated_at'])

        log_admin_action(request, 'slot_disabled', 'SlotMaster', slot.id, {
            'turf_id': turf.id,
            'turf_name': turf.name,
            'slot': f"{slot.start_time.strftime('%H:%M')}-{slot.end_time.strftime('%H:%M')}",
            'day': slot.get_day_of_week_display(),
        })

        logger.info(f"Slot #{slot.id} disabled by {request.user.username} on {turf.name}")

        return Response({
            'success': True,
            'message': 'Slot disabled',
            'slot_id': slot.id,
            'is_active': False,
        }, status=status.HTTP_200_OK)

    # ─── OWNER: ENABLE SLOT ───────────────────────────────────────────────────

    @action(detail=True, methods=['post'])
    def enable_slot(self, request, pk=None):
        """
        Owner re-enables a disabled SlotMaster (is_active = True).
        POST /api/turfs/turfs/{id}/enable_slot/
        Body: {"slot_id": 42}
        """
        turf = self.get_object()

        # Ownership check
        if turf.owner != request.user:
            return Response({
                'success': False,
                'error': 'You can only manage slots on your own turfs',
            }, status=status.HTTP_403_FORBIDDEN)

        slot_id = request.data.get('slot_id')
        if not slot_id:
            return Response({
                'success': False,
                'error': 'slot_id is required',
            }, status=status.HTTP_400_BAD_REQUEST)

        slot = SlotMaster.objects.filter(id=slot_id, turf=turf).first()
        if not slot:
            return Response({
                'success': False,
                'error': 'Slot not found for this turf',
            }, status=status.HTTP_404_NOT_FOUND)

        slot.is_active = True
        slot.save(update_fields=['is_active', 'updated_at'])

        log_admin_action(request, 'slot_enabled', 'SlotMaster', slot.id, {
            'turf_id': turf.id,
            'turf_name': turf.name,
            'slot': f"{slot.start_time.strftime('%H:%M')}-{slot.end_time.strftime('%H:%M')}",
            'day': slot.get_day_of_week_display(),
        })

        logger.info(f"Slot #{slot.id} enabled by {request.user.username} on {turf.name}")

        return Response({
            'success': True,
            'message': 'Slot enabled successfully',
            'slot_id': slot.id,
            'is_active': True,
        }, status=status.HTTP_200_OK)

    # ─── OWNER: CREATE OFFER ──────────────────────────────────────────────

    @action(detail=True, methods=['post'])
    def create_offer(self, request, pk=None):
        """
        Owner creates a SlotOffer for a specific slot.
        POST /api/turfs/turfs/{id}/create_offer/
        Body: {
            "slot_id": 42,
            "offer_type": "percentage",  // or "flat"
            "value": 20,
            "valid_from": "2026-02-20",
            "valid_until": "2026-03-20",
            "max_discount_cap": 100  // optional, for percentage only
        }
        """
        turf = self.get_object()

        # Ownership check
        if turf.owner != request.user:
            return Response({
                'success': False,
                'error': 'You can only create offers on your own turfs',
            }, status=status.HTTP_403_FORBIDDEN)

        slot_id = request.data.get('slot_id')
        offer_type = request.data.get('offer_type')
        value = request.data.get('value')
        valid_from_str = request.data.get('valid_from')
        valid_until_str = request.data.get('valid_until')
        max_cap = request.data.get('max_discount_cap')

        # Validate required fields
        if not all([slot_id, offer_type, value, valid_from_str, valid_until_str]):
            return Response({
                'success': False,
                'error': 'slot_id, offer_type, value, valid_from, valid_until are required',
            }, status=status.HTTP_400_BAD_REQUEST)

        if offer_type not in [OfferType.PERCENTAGE, OfferType.FLAT]:
            return Response({
                'success': False,
                'error': 'offer_type must be "percentage" or "flat"',
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            slot = SlotMaster.objects.get(id=slot_id, turf=turf)
        except SlotMaster.DoesNotExist:
            return Response({
                'success': False,
                'error': 'Slot not found for this turf',
            }, status=status.HTTP_404_NOT_FOUND)

        try:
            valid_from = datetime.strptime(valid_from_str, '%Y-%m-%d').date()
            valid_until = datetime.strptime(valid_until_str, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            return Response({
                'success': False,
                'error': 'Invalid date format. Use YYYY-MM-DD.',
            }, status=status.HTTP_400_BAD_REQUEST)

        if valid_from > valid_until:
            return Response({
                'success': False,
                'error': 'valid_from must be before valid_until',
            }, status=status.HTTP_400_BAD_REQUEST)

        # Offer validation: slot must be active (not disabled)
        if not slot.is_active:
            return Response({
                'success': False,
                'error': 'Cannot create offers on disabled slots',
                'error_code': 'slot_disabled',
            }, status=status.HTTP_400_BAD_REQUEST)

        # Offer validation: valid_from must not be in the past
        today = timezone.localdate()
        if valid_from < today:
            return Response({
                'success': False,
                'error': 'valid_from cannot be in the past',
            }, status=status.HTTP_400_BAD_REQUEST)

        # Offer validation: slot must not have active bookings for the offer period
        has_active_bookings = BookingSlot.objects.filter(
            slot_master=slot,
            booking_date__gte=valid_from,
            booking_date__lte=valid_until,
        ).exclude(
            booking__booking_status=BookingStatus.CANCELLED,
        ).exists()

        if has_active_bookings:
            return Response({
                'success': False,
                'error': 'Cannot create offers for slots with active bookings in the offer period',
                'error_code': 'slot_already_booked',
            }, status=status.HTTP_400_BAD_REQUEST)

        from decimal import Decimal
        try:
            value_dec = Decimal(str(value))
        except Exception:
            return Response({
                'success': False,
                'error': 'value must be a valid number',
            }, status=status.HTTP_400_BAD_REQUEST)

        offer = SlotOffer.objects.create(
            slot_master=slot,
            offer_type=offer_type,
            value=value_dec,
            max_discount_cap=Decimal(str(max_cap)) if max_cap else None,
            valid_from=valid_from,
            valid_until=valid_until,
            is_active=True,
        )

        log_admin_action(request, 'offer_created', 'SlotOffer', offer.id, {
            'turf_id': turf.id,
            'turf_name': turf.name,
            'slot_id': slot.id,
            'slot': f"{slot.start_time.strftime('%H:%M')}-{slot.end_time.strftime('%H:%M')}",
            'offer_type': offer_type,
            'value': str(value_dec),
            'valid_from': str(valid_from),
            'valid_until': str(valid_until),
        })

        logger.info(
            f"Offer #{offer.id} created by {request.user.username} "
            f"on slot {slot.id} ({turf.name}): {offer}"
        )

        return Response({
            'success': True,
            'message': 'Offer created successfully',
            'offer_id': offer.id,
            'offer_type': offer.offer_type,
            'value': str(offer.value),
            'valid_from': str(offer.valid_from),
            'valid_until': str(offer.valid_until),
        }, status=status.HTTP_201_CREATED)

    # ─── OWNER: DELETE (DEACTIVATE) OFFER ─────────────────────────────────

    @action(detail=True, methods=['post'])
    def delete_offer(self, request, pk=None):
        """
        Deactivate all active offers for a specific slot.
        POST /api/turfs/turfs/{id}/delete_offer/
        Body: { "slot_id": 42 }
        """
        turf = self.get_object()

        # Ownership check
        if turf.owner != request.user:
            return Response({
                'success': False,
                'error': 'You can only manage offers on your own turfs',
            }, status=status.HTTP_403_FORBIDDEN)

        slot_id = request.data.get('slot_id')
        if not slot_id:
            return Response({
                'success': False,
                'error': 'slot_id is required',
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            slot = SlotMaster.objects.get(id=slot_id, turf=turf)
        except SlotMaster.DoesNotExist:
            return Response({
                'success': False,
                'error': 'Slot not found for this turf',
            }, status=status.HTTP_404_NOT_FOUND)

        # Deactivate all active offers on this slot
        count = SlotOffer.objects.filter(
            slot_master=slot,
            is_active=True,
        ).update(is_active=False)

        log_admin_action(request, 'offer_deleted', 'SlotOffer', slot.id, {
            'turf_id': turf.id,
            'turf_name': turf.name,
            'slot_id': slot.id,
            'slot': f"{slot.start_time.strftime('%H:%M')}-{slot.end_time.strftime('%H:%M')}",
            'offers_deactivated': count,
        })

        logger.info(
            f"Offers deactivated by {request.user.username} "
            f"on slot {slot.id} ({turf.name}): {count} offers"
        )

        return Response({
            'success': True,
            'message': f'{count} offer(s) deactivated',
            'slot_id': slot.id,
        }, status=status.HTTP_200_OK)
