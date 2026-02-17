"""
Serializers for bookings app.
"""

from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, time

from rest_framework import serializers
from django.utils import timezone

from .models import Booking, BookingPreview, Payment, BookingStatus, PaymentStatus
from turfs.serializers import TurfListSerializer
from users.serializers import CustomUserBasicSerializer


class PaymentSerializer(serializers.ModelSerializer):
    """Serializer for Payment model."""
    
    class Meta:
        model = Payment
        fields = ['id', 'amount', 'payment_method', 'transaction_id', 'status', 'created_at', 'paid_at']
        read_only_fields = ['id', 'created_at']


class BookingListSerializer(serializers.ModelSerializer):
    """Simplified booking serializer for list view."""
    turf = TurfListSerializer(read_only=True)
    user = CustomUserBasicSerializer(read_only=True)
    
    class Meta:
        model = Booking
        fields = [
            'id', 'user', 'turf', 'booking_date', 'start_time', 'end_time',
            'total_price', 'final_price', 'booking_status', 'payment_status',
            'created_at'
        ]


class BookingDetailSerializer(serializers.ModelSerializer):
    """Detailed booking serializer."""
    turf = TurfListSerializer(read_only=True)
    user = CustomUserBasicSerializer(read_only=True)
    payment = PaymentSerializer(read_only=True)
    
    class Meta:
        model = Booking
        fields = [
            'id', 'user', 'turf', 'booking_date', 'start_time', 'end_time',
            'selected_slots',
            'price_per_hour', 'total_price', 'discount', 'final_price',
            'gst_amount', 'commission', 'platform_fee', 'gst_on_platform_fee',
            'owner_payout', 'payout_status',
            'booking_status', 'payment_status', 'notes', 'payment',
            'cancelled_reason', 'cancelled_at', 'cancelled_by_admin',
            'idempotency_key',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'user', 'created_at', 'updated_at']


class BookingCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating bookings."""
    turf_id = serializers.IntegerField()
    start_time = serializers.TimeField()
    end_time = serializers.TimeField()
    booking_date = serializers.DateField()
    
    class Meta:
        model = Booking
        fields = ['turf_id', 'booking_date', 'start_time', 'end_time', 'notes']
    
    def validate(self, data):
        """Validate booking dates and times."""
        booking_date = data.get('booking_date')
        start_time = data.get('start_time')
        end_time = data.get('end_time')
        
        # Check if booking date is not in the past
        if booking_date < timezone.now().date():
            raise serializers.ValidationError({'booking_date': 'Booking date must be in the future'})
        
        # Check if start time is before end time
        if start_time >= end_time:
            raise serializers.ValidationError({'start_time': 'Start time must be before end time'})
        
        # Check if booking is at least 30 minutes
        start_dt = datetime.combine(booking_date, start_time)
        end_dt = datetime.combine(booking_date, end_time)
        duration = (end_dt - start_dt).total_seconds() / 3600  # Convert to hours
        
        if duration < 0.5:
            raise serializers.ValidationError({'end_time': 'Booking must be at least 30 minutes'})
        
        return data
    
    def create(self, validated_data):
        """Create booking with calculated prices using Decimal."""
        from turfs.models import Turf
        
        turf_id = validated_data.pop('turf_id')
        turf = Turf.objects.get(id=turf_id)
        
        # Calculate duration using Decimal — no float()
        start_time = validated_data['start_time']
        end_time = validated_data['end_time']
        start_dt = datetime.combine(validated_data['booking_date'], start_time)
        end_dt = datetime.combine(validated_data['booking_date'], end_time)
        duration_seconds = Decimal(str((end_dt - start_dt).total_seconds()))
        duration_hours = (duration_seconds / Decimal('3600')).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        
        # Calculate prices with Decimal
        price_per_hour = Decimal(str(turf.price_per_hour))
        total_price = (price_per_hour * duration_hours).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        
        validated_data['turf'] = turf
        validated_data['user'] = self.context['request'].user
        validated_data['price_per_hour'] = price_per_hour
        validated_data['total_price'] = total_price
        validated_data['discount'] = Decimal('0.00')
        validated_data['final_price'] = total_price
        validated_data['booking_status'] = BookingStatus.PENDING
        validated_data['payment_status'] = PaymentStatus.PENDING
        
        booking = Booking.objects.create(**validated_data)
        return booking


class BookingUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating bookings."""
    
    class Meta:
        model = Booking
        fields = ['notes']


class BookingCancelSerializer(serializers.Serializer):
    """Serializer for cancelling bookings."""
    reason = serializers.CharField(required=False, allow_blank=True)
    
    def update(self, instance, validated_data):
        """Cancel the booking."""
        instance.cancel(
            reason=validated_data.get('reason', ''),
            cancelled_by_admin=False
        )
        return instance


class BookingConfirmSerializer(serializers.Serializer):
    """Serializer for confirming bookings."""
    
    def update(self, instance, validated_data):
        """Confirm the booking."""
        instance.confirm()
        return instance
