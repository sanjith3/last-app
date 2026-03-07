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
from bookings.models import Booking, BookingSlot, BookingStatus
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
            
        # Authenticated users: approved+active turfs for public browsing,
        # PLUS the user's own turfs at any status (needed for upload_image on pending turfs).
        return queryset.filter(
            Q(status=TurfStatus.APPROVED, is_active=True) | Q(owner=user)
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
            
            serializer = self.get_serializer(
                queryset, many=True,
                context={'request': request, 'include_stats': True},
            )
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

    # ─── OWNER DASHBOARD AGGREGATE STATS ─────────────────────────────────
    @action(detail=False, methods=['get'], permission_classes=[IsAuthenticated],
            url_path='owner_dashboard_stats')
    def owner_dashboard_stats(self, request):
        """
        Aggregated stats across all of the owner's turfs.
        Returns can_manage=False for owners with no approved turfs yet.
        GET /api/turfs/turfs/owner_dashboard_stats/
        """
        from django.db.models import Count, Sum, Avg, Q

        user = request.user
        today = timezone.localdate()

        owner_turfs = Turf.objects.filter(owner=user)
        total_turfs = owner_turfs.count()

        # ── Approval gate: no approved turfs → limited response ──────────
        has_approved_turf = owner_turfs.filter(status=TurfStatus.APPROVED).exists()
        if not has_approved_turf:
            pending_count = owner_turfs.filter(status=TurfStatus.PENDING).count()
            rejected_count = owner_turfs.filter(status=TurfStatus.REJECTED).count()
            suspended_count = owner_turfs.filter(status=TurfStatus.SUSPENDED).count()

            # Build per-turf status info so Flutter can show details
            turf_statuses = list(owner_turfs.values(
                'id', 'name', 'status', 'rejection_reason', 'created_at',
            ))

            return Response({
                'success': True,
                'can_manage': False,
                'message': (
                    'Your turf is under review. Our team will approve it within 24–48 hours. '
                    'You will receive a notification once approved.'
                    if pending_count > 0 else
                    'Your turf application was not approved. Please contact support for details.'
                ),
                'total_turfs': total_turfs,
                'pending_count': pending_count,
                'rejected_count': rejected_count,
                'suspended_count': suspended_count,
                'turf_statuses': turf_statuses,
            })

        if total_turfs == 0:
            return Response({
                'success': True,
                'can_manage': True,
                'total_turfs': 0,
                'total_bookings': 0,
                'today_bookings': 0,
                'today_revenue': '0.00',
                'total_revenue': '0.00',
                'avg_rating': 0,
            })

        confirmed_q = Q(booking_status__in=[
            BookingStatus.CONFIRMED, BookingStatus.COMPLETED,
        ])

        agg = Booking.objects.filter(
            turf__owner=user,
        ).filter(confirmed_q).aggregate(
            total_bookings=Count('id'),
            total_revenue=Sum('owner_payout'),
            today_bookings=Count('id', filter=Q(booking_date=today)),
            today_revenue=Sum('owner_payout', filter=Q(booking_date=today)),
        )

        review_agg = Review.objects.filter(
            turf__owner=user,
        ).aggregate(avg_rating=Avg('rating'))

        return Response({
            'success': True,
            'can_manage': True,
            'total_turfs': total_turfs,
            'total_bookings': agg['total_bookings'] or 0,
            'today_bookings': agg['today_bookings'] or 0,
            'today_revenue': str(agg['today_revenue'] or 0),
            'total_revenue': str(agg['total_revenue'] or 0),
            'avg_rating': round(review_agg['avg_rating'] or 0, 1),
            'per_turf_stats': [
                {
                    'id': turf.id,
                    'name': turf.name,
                    **{
                        k: (str(v) if 'revenue' in k else (v or 0))
                        for k, v in Booking.objects.filter(
                            turf=turf,
                        ).filter(confirmed_q).aggregate(
                            total_bookings=Count('id'),
                            total_revenue=Sum('owner_payout'),
                            today_bookings=Count('id', filter=Q(booking_date=today)),
                            today_revenue=Sum('owner_payout', filter=Q(booking_date=today)),
                        ).items()
                    },
                    'avg_rating': round(Review.objects.filter(turf=turf).aggregate(
                        avg=Avg('rating')
                    )['avg'] or 0, 1),
                    'slots_count': turf.slot_masters.filter(is_active=True).count(),
                    # Shutdown state
                    'is_shutdown': turf.is_shutdown,
                    'shutdown_start': str(turf.shutdown_start) if turf.shutdown_start else None,
                    'shutdown_end': str(turf.shutdown_end) if turf.shutdown_end else None,
                    'shutdown_reason': turf.shutdown_reason,
                }
                for turf in owner_turfs
            ],
        })

    # ─── OWNER: WEEKLY STATS ──────────────────────────────────────────────────
    @action(detail=False, methods=['get'], permission_classes=[IsAuthenticated],
            url_path='weekly_stats')
    def weekly_stats(self, request):
        """
        Current-week vs last-week revenue and booking comparison.
        GET /api/turfs/turfs/weekly_stats/
        """
        from datetime import timedelta
        from django.db.models import Count, Sum, Q

        user = request.user
        today = timezone.localdate()
        # Monday of current week
        start_week = today - timedelta(days=today.weekday())
        end_week = start_week + timedelta(days=6)
        start_last = start_week - timedelta(days=7)
        end_last = end_week - timedelta(days=7)

        confirmed_q = Q(booking_status__in=['confirmed', 'completed'])
        base_qs = Booking.objects.filter(turf__owner=user).filter(confirmed_q)

        curr = base_qs.filter(booking_date__range=(start_week, end_week)).aggregate(
            revenue=Sum('owner_payout'), bookings=Count('id'),
        )
        last = base_qs.filter(booking_date__range=(start_last, end_last)).aggregate(
            revenue=Sum('owner_payout'), bookings=Count('id'),
        )

        curr_rev = float(curr['revenue'] or 0)
        last_rev = float(last['revenue'] or 0)
        curr_bk = curr['bookings'] or 0
        last_bk = last['bookings'] or 0

        rev_change_pct = round((curr_rev - last_rev) / last_rev * 100, 1) if last_rev else 0
        bk_change = curr_bk - last_bk

        # Per-turf breakdown for turf cards
        owner_turfs = Turf.objects.filter(owner=user)
        per_turf = []
        for turf in owner_turfs:
            tc = base_qs.filter(turf=turf, booking_date__range=(start_week, end_week)).aggregate(
                revenue=Sum('owner_payout'), bookings=Count('id'),
            )
            tl = base_qs.filter(turf=turf, booking_date__range=(start_last, end_last)).aggregate(
                revenue=Sum('owner_payout'), bookings=Count('id'),
            )
            t_curr_rev = float(tc['revenue'] or 0)
            t_last_rev = float(tl['revenue'] or 0)
            t_curr_bk = tc['bookings'] or 0
            t_last_bk = tl['bookings'] or 0
            t_rev_pct = round((t_curr_rev - t_last_rev) / t_last_rev * 100, 1) if t_last_rev else 0
            per_turf.append({
                'id': turf.id,
                'weekly_revenue': t_curr_rev,
                'last_week_revenue': t_last_rev,
                'weekly_bookings': t_curr_bk,
                'last_week_bookings': t_last_bk,
                'revenue_change_pct': t_rev_pct,
                'booking_change': t_curr_bk - t_last_bk,
            })

        return Response({
            'success': True,
            'week_start': str(start_week),
            'week_end': str(end_week),
            'current_week': {'revenue': curr_rev, 'bookings': curr_bk},
            'last_week': {'revenue': last_rev, 'bookings': last_bk},
            'changes': {'revenue_percent': rev_change_pct, 'bookings_count': bk_change},
            'per_turf': per_turf,
        })

    # ─── OWNER: SHUTDOWN / REACTIVATE ─────────────────────────────────────────
    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated],
            url_path='owner_shutdown')
    def owner_shutdown(self, request, pk=None):
        """
        Owner shuts down turf for a date range. Notifies admin via audit log.
        POST /api/turfs/turfs/{id}/owner_shutdown/
        Body: {start_date, end_date, reason}
        """
        turf = self.get_object()
        if turf.owner != request.user:
            return Response({'success': False, 'error': 'Not your turf.'}, status=403)

        start_str = request.data.get('start_date')
        end_str = request.data.get('end_date')
        reason = request.data.get('reason', '').strip()

        if not start_str or not end_str or not reason:
            return Response({'success': False, 'error': 'start_date, end_date and reason are required.'}, status=400)

        from datetime import date as date_type
        try:
            start_date = date_type.fromisoformat(start_str)
            end_date = date_type.fromisoformat(end_str)
        except ValueError:
            return Response({'success': False, 'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=400)

        if end_date < start_date:
            return Response({'success': False, 'error': 'end_date must be >= start_date.'}, status=400)

        turf.owner_shutdown(start_date, end_date, reason)
        log_admin_action(request, 'turf_shutdown', 'Turf', turf.id, {
            'turf': turf.name, 'from': str(start_date), 'to': str(end_date), 'reason': reason,
        })

        return Response({
            'success': True,
            'message': f'{turf.name} is now shutdown from {start_date} to {end_date}.',
            'is_shutdown': True,
            'shutdown_start': str(start_date),
            'shutdown_end': str(end_date),
        })

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated],
            url_path='owner_reactivate')
    def owner_reactivate(self, request, pk=None):
        """
        Owner cancels a shutdown immediately — no admin approval needed.
        POST /api/turfs/turfs/{id}/owner_reactivate/
        """
        turf = self.get_object()
        if turf.owner != request.user:
            return Response({'success': False, 'error': 'Not your turf.'}, status=403)

        turf.owner_reactivate()
        log_admin_action(request, 'turf_reactivated', 'Turf', turf.id, {'turf': turf.name})

        return Response({'success': True, 'message': f'{turf.name} is now active again.', 'is_shutdown': False})

    # ─── OWNER: UPDATE TURF DETAILS (LIVE + AUDIT LOG) ───────────────────
    @action(detail=True, methods=['patch'], permission_classes=[IsAuthenticated],
            url_path='update_details')
    def update_details(self, request, pk=None):
        """
        Owner updates their turf details. Changes go live immediately.
        An edit history record is created for admin notification/audit trail.
        PATCH /api/turfs/turfs/{id}/update_details/
        """
        from .models import TurfEditHistory

        turf = self.get_object()
        if turf.owner != request.user:
            return Response({'success': False, 'error': 'Not your turf.'}, status=403)

        # ── Capture old values before any changes ──
        old_values = {
            'name': turf.name,
            'description': turf.description or '',
            'city': turf.city,
            'state': turf.state,
            'price_per_hour': str(turf.price_per_hour),
            'google_maps_share_link': turf.google_maps_share_link or '',
            'sports': sorted([s.name for s in turf.sports.all()]),
            'amenities': sorted([a.name for a in turf.amenities.all()]),
        }

        data = request.data

        # ── Apply scalar field updates ──
        scalar_fields = ['name', 'description', 'city', 'state', 'price_per_hour', 'google_maps_share_link']
        for field in scalar_fields:
            if field in data:
                setattr(turf, field, data[field])
        turf.save(update_fields=[f for f in scalar_fields if f in data] or scalar_fields)

        # ── Apply M2M sports ──
        if 'sports' in data:
            sport_names = data['sports'] if isinstance(data['sports'], list) else []
            sport_objs = Sport.objects.filter(name__in=sport_names)
            turf.sports.set(sport_objs)

        # ── Apply M2M amenities ──
        if 'amenities' in data:
            amenity_names = data['amenities'] if isinstance(data['amenities'], list) else []
            amenity_objs = Amenity.objects.filter(name__in=amenity_names)
            turf.amenities.set(amenity_objs)

        # ── Capture new values and diff ──
        new_values = {
            'name': turf.name,
            'description': turf.description or '',
            'city': turf.city,
            'state': turf.state,
            'price_per_hour': str(turf.price_per_hour),
            'google_maps_share_link': turf.google_maps_share_link or '',
            'sports': sorted([s.name for s in turf.sports.all()]),
            'amenities': sorted([a.name for a in turf.amenities.all()]),
        }

        changes = {}
        for field, old_val in old_values.items():
            new_val = new_values[field]
            if old_val != new_val:
                changes[field] = {'old': old_val, 'new': new_val}

        # ── Record edit history (even if no changes — for audit) ──
        def get_client_ip(req):
            x_forwarded = req.META.get('HTTP_X_FORWARDED_FOR')
            if x_forwarded:
                return x_forwarded.split(',')[0].strip()
            return req.META.get('REMOTE_ADDR')

        if changes:
            TurfEditHistory.objects.create(
                turf=turf,
                owner=request.user,
                changes=changes,
                ip_address=get_client_ip(request),
            )

        serializer = TurfDetailSerializer(turf, context={'request': request})
        return Response({
            'success': True,
            'message': 'Turf details updated successfully.' + (
                ' Admin has been notified of your changes.' if changes else ''
            ),
            'changes_recorded': len(changes),
            'turf': serializer.data,
        })

    # ─── PHOTO MANAGEMENT ACTIONS ────────────────────────────────────────────

    @action(detail=True, methods=['get'], permission_classes=[IsAuthenticated],
            url_path='fetch_images')
    def fetch_images(self, request, pk=None):
        """
        Return all TurfImage records with real IDs for this turf.
        GET /api/turfs/turfs/{id}/fetch_images/
        Used by Flutter edit screen to get real image IDs for delete/set-cover.
        """
        from .models import TurfImage
        turf = self.get_object()
        images = TurfImage.objects.filter(turf=turf).order_by('-is_cover', 'id')
        data = []
        for img in images:
            data.append({
                'image_id': img.id,
                'image_url': request.build_absolute_uri(img.image.url),
                'is_cover': img.is_cover,
                'caption': img.caption or '',
            })
        return Response({'success': True, 'images': data, 'count': len(data)})

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated],
            url_path='upload_image')
    def upload_image(self, request, pk=None):
        """
        Upload a new photo for the turf.
        POST /api/turfs/turfs/{id}/upload_image/   (multipart/form-data, field='image')
        """
        from .models import TurfImage, TurfEditHistory

        turf = self.get_object()
        if turf.owner != request.user:
            return Response({'success': False, 'error': 'Not your turf.'}, status=403)

        if 'image' not in request.FILES:
            return Response({'success': False, 'error': 'No image file provided.'}, status=400)

        image_file = request.FILES['image']

        # Basic MIME validation
        allowed_types = {'image/jpeg', 'image/jpg', 'image/png', 'image/webp'}
        ct = getattr(image_file, 'content_type', '').lower()
        if ct not in allowed_types:
            return Response({'success': False, 'error': 'Invalid file type. Use JPEG, PNG or WEBP.'}, status=400)

        # First image becomes cover automatically
        is_first = not TurfImage.objects.filter(turf=turf).exists()

        turf_image = TurfImage.objects.create(
            turf=turf,
            image=image_file,
            caption=request.data.get('caption', ''),
            is_cover=is_first,
        )

        # Audit log
        def _get_ip(req):
            x = req.META.get('HTTP_X_FORWARDED_FOR')
            return x.split(',')[0].strip() if x else req.META.get('REMOTE_ADDR')

        TurfEditHistory.objects.create(
            turf=turf,
            owner=request.user,
            changes={'action': 'image_added', 'image_id': turf_image.id, 'is_cover': is_first},
            ip_address=_get_ip(request),
        )

        return Response({
            'success': True,
            'image_id': turf_image.id,
            'image_url': request.build_absolute_uri(turf_image.image.url),
            'is_cover': turf_image.is_cover,
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['delete'], permission_classes=[IsAuthenticated],
            url_path=r'delete_image/(?P<image_id>\d+)')
    def delete_image(self, request, pk=None, image_id=None):
        """
        Delete a photo by its ID.
        DELETE /api/turfs/turfs/{id}/delete_image/{image_id}/
        """
        from .models import TurfImage, TurfEditHistory

        turf = self.get_object()
        if turf.owner != request.user:
            return Response({'success': False, 'error': 'Not your turf.'}, status=403)

        try:
            img = TurfImage.objects.get(id=image_id, turf=turf)
        except TurfImage.DoesNotExist:
            return Response({'success': False, 'error': 'Image not found.'}, status=404)

        was_cover = img.is_cover
        img_id_saved = img.id

        # Delete the file + DB record
        img.image.delete(save=False)
        img.delete()

        # If the deleted image was the cover, promote the next one
        if was_cover:
            next_img = TurfImage.objects.filter(turf=turf).first()
            if next_img:
                next_img.is_cover = True
                next_img.save(update_fields=['is_cover'])

        def _get_ip(req):
            x = req.META.get('HTTP_X_FORWARDED_FOR')
            return x.split(',')[0].strip() if x else req.META.get('REMOTE_ADDR')

        TurfEditHistory.objects.create(
            turf=turf,
            owner=request.user,
            changes={'action': 'image_deleted', 'image_id': img_id_saved, 'was_cover': was_cover},
            ip_address=_get_ip(request),
        )

        return Response({'success': True, 'message': 'Photo deleted.'})

    @action(detail=True, methods=['patch'], permission_classes=[IsAuthenticated],
            url_path=r'set_cover/(?P<image_id>\d+)')
    def set_cover_image(self, request, pk=None, image_id=None):
        """
        Set a photo as the cover image.
        PATCH /api/turfs/turfs/{id}/set_cover/{image_id}/
        """
        from .models import TurfImage, TurfEditHistory

        turf = self.get_object()
        if turf.owner != request.user:
            return Response({'success': False, 'error': 'Not your turf.'}, status=403)

        try:
            new_cover = TurfImage.objects.get(id=image_id, turf=turf)
        except TurfImage.DoesNotExist:
            return Response({'success': False, 'error': 'Image not found.'}, status=404)

        # Clear existing covers, set new one
        TurfImage.objects.filter(turf=turf, is_cover=True).update(is_cover=False)
        new_cover.is_cover = True
        new_cover.save(update_fields=['is_cover'])

        def _get_ip(req):
            x = req.META.get('HTTP_X_FORWARDED_FOR')
            return x.split(',')[0].strip() if x else req.META.get('REMOTE_ADDR')

        TurfEditHistory.objects.create(
            turf=turf,
            owner=request.user,
            changes={'action': 'cover_changed', 'new_cover_id': new_cover.id},
            ip_address=_get_ip(request),
        )

        return Response({'success': True, 'message': 'Cover image updated.'})

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

    # ─── OWNER: CROP / REPLACE IMAGE ──────────────────────────────────────────

    @action(detail=True, methods=['post'], url_path='crop_image')
    def crop_image(self, request, pk=None):
        """
        Replace an image with its cropped version and save optional crop metadata.
        POST /api/turfs/turfs/{id}/crop_image/
        Multipart body:
          image_id  — int, required
          image     — file, required (already-cropped JPEG from Flutter)
          crop_x    — float 0-1 (optional metadata)
          crop_y    — float 0-1
          crop_width
          crop_height
        """
        turf = self.get_object()

        if turf.owner != request.user:
            return Response(
                {'success': False, 'error': 'You can only edit your own turf images'},
                status=status.HTTP_403_FORBIDDEN,
            )

        image_id = request.data.get('image_id')
        if not image_id:
            return Response(
                {'success': False, 'error': 'image_id is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            turf_image = TurfImage.objects.get(id=image_id, turf=turf)
        except TurfImage.DoesNotExist:
            return Response(
                {'success': False, 'error': 'Image not found for this turf'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Replace the image file with the uploaded cropped version
        if 'image' in request.FILES:
            if turf_image.image:
                try:
                    turf_image.image.delete(save=False)
                except Exception:
                    pass  # storage errors are non-fatal
            turf_image.image = request.FILES['image']

        # Persist optional crop metadata
        for field in ('crop_x', 'crop_y', 'crop_width', 'crop_height'):
            if field in request.data:
                try:
                    setattr(turf_image, field, float(request.data[field]))
                except (ValueError, TypeError):
                    pass

        turf_image.save()

        # Build absolute URL for the response
        image_url = None
        if turf_image.image:
            try:
                image_url = request.build_absolute_uri(turf_image.image.url)
            except Exception:
                image_url = turf_image.image.url

        logger.info(
            f"Image #{turf_image.id} cropped by {request.user.username} on turf {turf.id}"
        )

        return Response({
            'success': True,
            'image_id': turf_image.id,
            'image_url': image_url,
            'crop_settings': {
                'x': turf_image.crop_x,
                'y': turf_image.crop_y,
                'width': turf_image.crop_width,
                'height': turf_image.crop_height,
            },
        }, status=status.HTTP_200_OK)
