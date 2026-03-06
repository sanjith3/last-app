"""
Serializers for users app.
"""

from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import TurfOwner

User = get_user_model()


class CustomUserBasicSerializer(serializers.ModelSerializer):
    """Basic user info for nested serialization."""
    
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'phone_number', 'profile_picture']
        read_only_fields = ['id']


class CustomUserRegistrationSerializer(serializers.ModelSerializer):
    """Serializer for user registration."""
    password = serializers.CharField(write_only=True, min_length=6)
    password_confirm = serializers.CharField(write_only=True, min_length=6)
    
    class Meta:
        model = User
        fields = ['username', 'email', 'password', 'password_confirm', 'first_name', 'last_name', 'phone_number', 'role']
    
    def validate(self, data):
        if data['password'] != data.pop('password_confirm'):
            raise serializers.ValidationError({'password': 'Passwords do not match'})
        return data
    
    def create(self, validated_data):
        user = User(**validated_data)
        user.set_password(validated_data['password'])
        user.save()
        return user


class CustomUserDetailSerializer(serializers.ModelSerializer):
    """Detailed user info — stats computed live from DB (no stale counters)."""
    available_credits = serializers.SerializerMethodField()
    total_bookings = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name',
            'phone_number', 'role', 'profile_picture', 'bio', 'is_verified',
            'total_bookings', 'total_credits', 'used_credits', 'available_credits',
            'referral_code', 'wallet_balance', 'referral_cashback_earned',
            'total_referrals', 'qualified_referrals',
            'first_booking_completed',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_total_bookings(self, obj):
        """
        Live count from Booking table — only confirmed/completed bookings.
        Excludes pending and cancelled so the profile shows meaningful bookings.
        """
        from bookings.models import Booking, BookingStatus
        return Booking.objects.filter(
            user=obj,
            booking_status__in=[BookingStatus.CONFIRMED, BookingStatus.COMPLETED],
        ).count()

    def get_available_credits(self, obj):
        """Derived from stored credit counters (these ARE kept in sync by booking flow)."""
        return max(0, obj.total_credits - obj.used_credits)



class TurfOwnerProfileSerializer(serializers.ModelSerializer):
    """Serializer for turf owner profile."""
    user = CustomUserBasicSerializer(read_only=True)
    total_turfs = serializers.SerializerMethodField()
    
    class Meta:
        model = TurfOwner
        fields = ['id', 'user', 'bank_account', 'ifsc_code', 'gst_number', 
                  'total_turfs', 'total_bookings', 'total_revenue', 'rating', 'created_at']
        read_only_fields = ['id', 'total_turfs', 'total_bookings', 'total_revenue', 'created_at']

    def get_total_turfs(self, obj):
        """Always return live count instead of stored value."""
        return obj.user.turfs.count()


class TurfOwnerRegistrationSerializer(serializers.Serializer):
    """Serializer for turf owner registration with Google Maps link.
    
    Supports multi-turf: if the email belongs to an existing turf_owner,
    we reuse their account instead of blocking registration.
    """
    username = serializers.CharField(min_length=3)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=6, required=False)
    password_confirm = serializers.CharField(write_only=True, min_length=6, required=False)
    first_name = serializers.CharField()
    last_name = serializers.CharField(required=False, allow_blank=True)
    phone_number = serializers.CharField()
    google_maps_share_link = serializers.URLField()
    
    def validate(self, data):
        # Look up existing user by phone number first, then email
        phone = data.get('phone_number', '').strip()
        existing_user = None
        
        if phone:
            existing_user = User.objects.filter(phone_number=phone).first()
        if not existing_user:
            existing_user = User.objects.filter(email=data['email']).first()
        
        if existing_user:
            # Existing user found — upgrade role if needed, reuse account
            if existing_user.role not in ('turf_owner', 'admin'):
                existing_user.role = 'turf_owner'
                existing_user.save(update_fields=['role'])
            data['_existing_user'] = existing_user
            return data
        
        # New user — require password
        if not data.get('password'):
            raise serializers.ValidationError({'password': 'Password is required for new registration'})
        if data.get('password') != data.pop('password_confirm', None):
            raise serializers.ValidationError({'password': 'Passwords do not match'})
        
        # Check username uniqueness only for new users
        if User.objects.filter(username=data['username']).exists():
            raise serializers.ValidationError({'username': 'Username already exists'})
        
        return data
    
    def create(self, validated_data):
        existing_user = validated_data.pop('_existing_user', None)
        
        if existing_user:
            # Existing owner — reuse account, ensure TurfOwner profile exists
            turf_owner, _ = TurfOwner.objects.get_or_create(user=existing_user)
            return {
                'user': existing_user,
                'turf_owner': turf_owner,
                'google_maps_link': validated_data['google_maps_share_link'],
                'is_existing_owner': True,
            }
        
        # New user — create account with correct role
        validated_data.pop('password_confirm', None)
        google_maps_link = validated_data.pop('google_maps_share_link')
        
        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            password=validated_data['password'],
            first_name=validated_data['first_name'],
            last_name=validated_data['last_name'],
            phone_number=validated_data['phone_number'],
            role='turf_owner',  # Correct role
        )
        
        # Create turf owner profile
        turf_owner = TurfOwner.objects.create(user=user)
        
        return {
            'user': user,
            'turf_owner': turf_owner,
            'google_maps_link': google_maps_link,
            'is_existing_owner': False,
        }


class LoginSerializer(serializers.Serializer):
    """Serializer for user login."""
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)


class ChangePasswordSerializer(serializers.Serializer):
    """Serializer for changing password."""
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=6)
    new_password_confirm = serializers.CharField(write_only=True, min_length=6)
    
    def validate(self, data):
        if data['new_password'] != data['new_password_confirm']:
            raise serializers.ValidationError({'new_password': 'Passwords do not match'})
        return data
