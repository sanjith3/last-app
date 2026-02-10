"""
Views for bookings app.
"""

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q
from datetime import datetime, timedelta

from .models import Booking, Payment, BookingStatus, PaymentStatus
from .serializers import (
    BookingListSerializer,
    BookingDetailSerializer,
    BookingCreateSerializer,
    BookingUpdateSerializer,
    BookingCancelSerializer,
    BookingConfirmSerializer,
)


class BookingViewSet(viewsets.ModelViewSet):
    """ViewSet for bookings."""
    permission_classes = [IsAuthenticated]
    lookup_field = 'pk'
    
    def get_queryset(self):
        """Filter bookings based on user role."""
        user = self.request.user
        
        if user.role == 'turf_owner':
            # Turf owners see bookings for their turfs
            return Booking.objects.filter(
                turf__owner=user
            ).select_related('user', 'turf', 'payment').order_by('-created_at')
        
        elif user.role == 'admin':
            # Admins see all bookings
            return Booking.objects.all().select_related('user', 'turf', 'payment').order_by('-created_at')
        
        else:
            # Normal users see only their bookings
            return Booking.objects.filter(
                user=user
            ).select_related('user', 'turf', 'payment').order_by('-created_at')
    
    def get_serializer_class(self):
        if self.action == 'create':
            return BookingCreateSerializer
        elif self.action == 'retrieve':
            return BookingDetailSerializer
        elif self.action == 'update' or self.action == 'partial_update':
            return BookingUpdateSerializer
        elif self.action == 'cancel':
            return BookingCancelSerializer
        elif self.action == 'confirm':
            return BookingConfirmSerializer
        return BookingListSerializer
    
    def create(self, request, *args, **kwargs):
        """
        Create a new booking.
        Link: POST /api/bookings/bookings/
        
        Required fields:
        - turf_id (int)
        - booking_date (YYYY-MM-DD)
        - start_time (HH:MM)
        - end_time (HH:MM)
        
        Optional:
        - notes (str)
        """
        serializer = self.get_serializer(data=request.data, context={'request': request})
        
        if serializer.is_valid():
            booking = serializer.save()
            return Response({
                'success': True,
                'message': 'Booking created successfully',
                'data': BookingDetailSerializer(booking).data
            }, status=status.HTTP_201_CREATED)
        
        return Response({
            'success': False,
            'errors': serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)
    
    def list(self, request, *args, **kwargs):
        """
        List bookings with optional filtering.
        Link: GET /api/bookings/bookings/?status=confirmed&date=2024-01-15
        
        Query params:
        - status (pending|confirmed|completed|cancelled)
        - date (YYYY-MM-DD) - filter by booking date
        - turf_id (int) - turf owner can filter by turf
        """
        queryset = self.get_queryset()
        
        # Status filter
        status_filter = request.query_params.get('status', '').strip()
        if status_filter:
            queryset = queryset.filter(booking_status=status_filter)
        
        # Date filter
        date_filter = request.query_params.get('date', '').strip()
        if date_filter:
            try:
                filter_date = datetime.strptime(date_filter, '%Y-%m-%d').date()
                queryset = queryset.filter(booking_date=filter_date)
            except ValueError:
                pass
        
        # Turf filter (for turf owners)
        if request.user.role == 'turf_owner':
            turf_id = request.query_params.get('turf_id', '').strip()
            if turf_id:
                queryset = queryset.filter(turf_id=int(turf_id))
        
        serializer = self.get_serializer(queryset, many=True)
        
        return Response({
            'success': True,
            'count': queryset.count(),
            'results': serializer.data
        }, status=status.HTTP_200_OK)
    
    def retrieve(self, request, *args, **kwargs):
        """Get booking details."""
        booking = self.get_object()
        serializer = self.get_serializer(booking)
        
        return Response({
            'success': True,
            'data': serializer.data
        }, status=status.HTTP_200_OK)
    
    def update(self, request, *args, **kwargs):
        """Update booking (notes only)."""
        booking = self.get_object()
        
        # Users can only update their own bookings
        if request.user != booking.user and request.user.role != 'admin':
            return Response({
                'success': False,
                'error': 'You can only update your own bookings'
            }, status=status.HTTP_403_FORBIDDEN)
        
        serializer = self.get_serializer(booking, data=request.data, partial=True)
        if serializer.is_valid():
            booking = serializer.save()
            return Response({
                'success': True,
                'message': 'Booking updated successfully',
                'data': BookingDetailSerializer(booking).data
            }, status=status.HTTP_200_OK)
        
        return Response({
            'success': False,
            'errors': serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['post'])
    def confirm(self, request, pk=None):
        """
        Confirm a booking (admin or turf owner).
        Link: POST /api/bookings/bookings/{id}/confirm/
        """
        booking = self.get_object()
        
        # Only turf owner or admin can confirm
        if request.user.role == 'turf_owner' and booking.turf.owner != request.user:
            return Response({
                'success': False,
                'error': 'Only the turf owner can confirm this booking'
            }, status=status.HTTP_403_FORBIDDEN)
        
        if booking.booking_status != BookingStatus.PENDING:
            return Response({
                'success': False,
                'error': f'Booking is already {booking.booking_status}'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        booking.confirm()
        
        return Response({
            'success': True,
            'message': 'Booking confirmed successfully',
            'data': BookingDetailSerializer(booking).data
        }, status=status.HTTP_200_OK)
    
    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """
        Cancel a booking.
        Link: POST /api/bookings/bookings/{id}/cancel/
        
        Request body:
        {
            "reason": "Optional cancellation reason"
        }
        """
        booking = self.get_object()
        
        # Users can only cancel their own bookings
        if request.user != booking.user and request.user.role not in ['admin', 'turf_owner']:
            return Response({
                'success': False,
                'error': 'You can only cancel your own bookings'
            }, status=status.HTTP_403_FORBIDDEN)
        
        # Check if booking can be cancelled
        if booking.booking_status == BookingStatus.CANCELLED:
            return Response({
                'success': False,
                'error': 'Booking is already cancelled'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        if booking.booking_status == BookingStatus.COMPLETED:
            return Response({
                'success': False,
                'error': 'Completed bookings cannot be cancelled'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Cancel the booking
        reason = request.data.get('reason', '')
        cancelled_by_admin = request.user.role in ['admin', 'turf_owner']
        booking.cancel(reason=reason, cancelled_by_admin=cancelled_by_admin)
        
        return Response({
            'success': True,
            'message': 'Booking cancelled successfully',
            'data': BookingDetailSerializer(booking).data
        }, status=status.HTTP_200_OK)
    
    @action(detail=False, methods=['get'])
    def my_bookings(self, request):
        """
        Get current user's bookings with status breakdown.
        Link: GET /api/bookings/bookings/my_bookings/
        """
        bookings = self.get_queryset()
        
        upcoming = bookings.filter(
            booking_date__gte=datetime.now().date(),
            booking_status__in=[BookingStatus.CONFIRMED, BookingStatus.PENDING]
        ).order_by('booking_date', 'start_time')
        
        completed = bookings.filter(booking_status=BookingStatus.COMPLETED).order_by('-booking_date')
        
        cancelled = bookings.filter(booking_status=BookingStatus.CANCELLED).order_by('-booking_date')
        
        return Response({
            'success': True,
            'upcoming': {
                'count': upcoming.count(),
                'data': BookingListSerializer(upcoming, many=True).data
            },
            'completed': {
                'count': completed.count(),
                'data': BookingListSerializer(completed, many=True).data
            },
            'cancelled': {
                'count': cancelled.count(),
                'data': BookingListSerializer(cancelled, many=True).data
            }
        }, status=status.HTTP_200_OK)
    
    @action(detail=False, methods=['get'])
    def availability(self, request):
        """
        Check turf availability for a specific date and time.
        Link: GET /api/bookings/bookings/availability/?turf_id=1&booking_date=2024-01-15&start_time=14:00&end_time=16:00
        """
        from turfs.models import Turf
        
        turf_id = request.query_params.get('turf_id')
        booking_date = request.query_params.get('booking_date')
        start_time = request.query_params.get('start_time')
        end_time = request.query_params.get('end_time')
        
        if not all([turf_id, booking_date, start_time, end_time]):
            return Response({
                'success': False,
                'error': 'Missing required parameters'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            turf = Turf.objects.get(id=turf_id)
            booking_dt = datetime.strptime(booking_date, '%Y-%m-%d').date()
            start_dt = datetime.strptime(start_time, '%H:%M').time()
            end_dt = datetime.strptime(end_time, '%H:%M').time()
            
            # Check for conflicting bookings
            conflicting = Booking.objects.filter(
                turf=turf,
                booking_date=booking_dt,
                booking_status__in=[BookingStatus.CONFIRMED, BookingStatus.PENDING],
            ).exclude(booking_status=BookingStatus.CANCELLED)
            
            is_available = True
            for booking in conflicting:
                # Check if time slots overlap
                if (start_dt < booking.end_time and end_dt > booking.start_time):
                    is_available = False
                    break
            
            return Response({
                'success': True,
                'turf_id': turf_id,
                'booking_date': booking_date,
                'start_time': start_time,
                'end_time': end_time,
                'is_available': is_available,
                'conflicting_bookings': conflicting.count()
            }, status=status.HTTP_200_OK)
        
        except Turf.DoesNotExist:
            return Response({
                'success': False,
                'error': 'Turf not found'
            }, status=status.HTTP_404_NOT_FOUND)
        except ValueError:
            return Response({
                'success': False,
                'error': 'Invalid date/time format'
            }, status=status.HTTP_400_BAD_REQUEST)
