"""
Serializers for turfs app.
"""

from rest_framework import serializers
from .models import Turf, TurfImage, Sport, Amenity, Review
from users.serializers import CustomUserBasicSerializer
from core.utils import extract_coordinates_from_google_maps_share_link


class SportSerializer(serializers.ModelSerializer):
    """Serializer for Sport model."""
    
    class Meta:
        model = Sport
        fields = ['id', 'name', 'icon', 'description']


class AmenitySerializer(serializers.ModelSerializer):
    """Serializer for Amenity model."""
    
    class Meta:
        model = Amenity
        fields = ['id', 'name', 'icon', 'description']


class TurfImageSerializer(serializers.ModelSerializer):
    """Serializer for turf images."""
    
    class Meta:
        model = TurfImage
        fields = ['id', 'image', 'caption', 'is_cover', 'uploaded_at']
        read_only_fields = ['id', 'uploaded_at']


class TurfListSerializer(serializers.ModelSerializer):
    """Simplified turf serializer for list view."""
    sports = SportSerializer(many=True, read_only=True)
    amenities = AmenitySerializer(many=True, read_only=True)
    images = TurfImageSerializer(many=True, read_only=True)
    cover_image = serializers.SerializerMethodField()
    distance = serializers.FloatField(read_only=True, required=False)
    
    class Meta:
        model = Turf
        fields = [
            'id', 'name', 'city', 'price_per_hour', 'rating', 'review_count',
            'address', 'latitude', 'longitude', 'sports', 'amenities',
            'images', 'cover_image', 'distance', 'review_count'
        ]
    
    def get_cover_image(self, obj):
        """Get the cover image URL."""
        cover = obj.images.filter(is_cover=True).first()
        if cover:
            return cover.image.url
        elif obj.images.exists():
            return obj.images.first().image.url
        return None


class TurfDetailSerializer(serializers.ModelSerializer):
    """Detailed turf serializer."""
    owner = CustomUserBasicSerializer(read_only=True)
    sports = SportSerializer(many=True, read_only=True)
    amenities = AmenitySerializer(many=True, read_only=True)
    images = TurfImageSerializer(many=True, read_only=True)
    reviews = serializers.SerializerMethodField()
    distance = serializers.FloatField(read_only=True, required=False)
    
    class Meta:
        model = Turf
        fields = [
            'id', 'owner', 'name', 'description', 'address', 'city', 'state',
            'postal_code', 'latitude', 'longitude', 'price_per_hour', 'max_players',
            'status', 'rating', 'review_count', 'sports', 'amenities', 'images',
            'reviews', 'is_active', 'google_maps_share_link', 'distance',
            'created_at', 'updated_at', 'approved_at'
        ]
        read_only_fields = ['id', 'owner', 'status', 'created_at', 'updated_at', 'approved_at']
    
    def get_reviews(self, obj):
        """Get recent reviews."""
        reviews = obj.reviews.all()[:5]  # Get last 5 reviews
        return ReviewSerializer(reviews, many=True).data


class TurfCreateUpdateSerializer(serializers.ModelSerializer):
    """Serializer for creating/updating turfs."""
    sport_ids = serializers.PrimaryKeyRelatedField(
        queryset=Sport.objects.all(),
        many=True,
        write_only=True,
        required=False
    )
    amenity_ids = serializers.PrimaryKeyRelatedField(
        queryset=Amenity.objects.all(),
        many=True,
        write_only=True,
        required=False
    )
    google_maps_share_link = serializers.URLField(required=True)
    
    class Meta:
        model = Turf
        fields = [
            'name', 'description', 'address', 'city', 'state', 'postal_code',
            'price_per_hour', 'max_players', 'google_maps_share_link',
            'sport_ids', 'amenity_ids'
        ]
    
    def validate_google_maps_share_link(self, value):
        """Validate and extract coordinates from Google Maps link."""
        result = extract_coordinates_from_google_maps_share_link(value)
        if not result['success']:
            raise serializers.ValidationError(result['message'])
        return value
    
    def create(self, validated_data):
        """Create turf and extract coordinates from Google Maps link."""
        sport_ids = validated_data.pop('sport_ids', [])
        amenity_ids = validated_data.pop('amenity_ids', [])
        google_maps_link = validated_data['google_maps_share_link']
        
        # Extract coordinates
        coords = extract_coordinates_from_google_maps_share_link(google_maps_link)
        validated_data['latitude'] = coords['latitude']
        validated_data['longitude'] = coords['longitude']
        
        # Set owner from request
        validated_data['owner'] = self.context['request'].user
        
        turf = Turf.objects.create(**validated_data)
        
        # Add related objects
        if sport_ids:
            turf.sports.set(sport_ids)
        if amenity_ids:
            turf.amenities.set(amenity_ids)
        
        return turf
    
    def update(self, instance, validated_data):
        """Update turf and coordinates if Google Maps link changed."""
        sport_ids = validated_data.pop('sport_ids', None)
        amenity_ids = validated_data.pop('amenity_ids', None)
        
        # Update fields
        for attr, value in validated_data.items():
            if attr == 'google_maps_share_link':
                coords = extract_coordinates_from_google_maps_share_link(value)
                instance.latitude = coords['latitude']
                instance.longitude = coords['longitude']
            setattr(instance, attr, value)
        
        instance.save()
        
        # Update many-to-many relations
        if sport_ids is not None:
            instance.sports.set(sport_ids)
        if amenity_ids is not None:
            instance.amenities.set(amenity_ids)
        
        return instance


class TurfRegistrationSerializer(serializers.ModelSerializer):
    """Serializer for turf owner registration - accepts Google Maps link and creates turf with status PENDING."""
    google_maps_share_link = serializers.URLField()
    sport_ids = serializers.PrimaryKeyRelatedField(
        queryset=Sport.objects.all(),
        many=True,
        write_only=True
    )
    amenity_ids = serializers.PrimaryKeyRelatedField(
        queryset=Amenity.objects.all(),
        many=True,
        write_only=True,
        required=False
    )
    
    class Meta:
        model = Turf
        fields = [
            'name', 'description', 'address', 'city', 'state', 'postal_code',
            'price_per_hour', 'max_players', 'google_maps_share_link',
            'sport_ids', 'amenity_ids'
        ]
    
    def validate_google_maps_share_link(self, value):
        """Validate and extract coordinates from Google Maps link."""
        result = extract_coordinates_from_google_maps_share_link(value)
        if not result['success']:
            raise serializers.ValidationError(result['message'])
        return value
    
    def create(self, validated_data):
        """Create turf with PENDING status."""
        sport_ids = validated_data.pop('sport_ids')
        amenity_ids = validated_data.pop('amenity_ids', [])
        google_maps_link = validated_data['google_maps_share_link']
        
        # Extract coordinates
        coords = extract_coordinates_from_google_maps_share_link(google_maps_link)
        validated_data['latitude'] = coords['latitude']
        validated_data['longitude'] = coords['longitude']
        
        # Set owner from request
        validated_data['owner'] = self.context['request'].user
        validated_data['status'] = 'pending'
        
        turf = Turf.objects.create(**validated_data)
        turf.sports.set(sport_ids)
        if amenity_ids:
            turf.amenities.set(amenity_ids)
        
        return turf


class ReviewSerializer(serializers.ModelSerializer):
    """Serializer for reviews."""
    user = CustomUserBasicSerializer(read_only=True)
    
    class Meta:
        model = Review
        fields = ['id', 'user', 'rating', 'comment', 'created_at', 'updated_at']
        read_only_fields = ['id', 'user', 'created_at', 'updated_at']


class ReviewCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating reviews."""
    
    class Meta:
        model = Review
        fields = ['rating', 'comment']
    
    def create(self, validated_data):
        validated_data['user'] = self.context['request'].user
        validated_data['turf_id'] = self.context['view'].kwargs.get('turf_pk')
        return Review.objects.create(**validated_data)
