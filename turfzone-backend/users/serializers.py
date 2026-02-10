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
    """Detailed user info."""
    
    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name',
            'phone_number', 'role', 'profile_picture', 'bio', 'is_verified',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class TurfOwnerProfileSerializer(serializers.ModelSerializer):
    """Serializer for turf owner profile."""
    user = CustomUserBasicSerializer(read_only=True)
    
    class Meta:
        model = TurfOwner
        fields = ['id', 'user', 'bank_account', 'ifsc_code', 'gst_number', 
                  'total_turfs', 'total_bookings', 'total_revenue', 'rating', 'created_at']
        read_only_fields = ['id', 'total_turfs', 'total_bookings', 'total_revenue', 'created_at']


class TurfOwnerRegistrationSerializer(serializers.Serializer):
    """Serializer for turf owner registration with Google Maps link."""
    username = serializers.CharField(min_length=3)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=6)
    password_confirm = serializers.CharField(write_only=True, min_length=6)
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    phone_number = serializers.CharField()
    google_maps_share_link = serializers.URLField()
    
    def validate(self, data):
        if data['password'] != data.pop('password_confirm'):
            raise serializers.ValidationError({'password': 'Passwords do not match'})
        
        if User.objects.filter(username=data['username']).exists():
            raise serializers.ValidationError({'username': 'Username already exists'})
        
        if User.objects.filter(email=data['email']).exists():
            raise serializers.ValidationError({'email': 'Email already registered'})
        
        return data
    
    def create(self, validated_data):
        # Create user
        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            password=validated_data['password'],
            first_name=validated_data['first_name'],
            last_name=validated_data['last_name'],
            phone_number=validated_data['phone_number'],
            role='user'
        )
        
        # Create turf owner profile
        turf_owner = TurfOwner.objects.create(user=user)
        
        return {'user': user, 'turf_owner': turf_owner, 'google_maps_link': validated_data['google_maps_share_link']}


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
