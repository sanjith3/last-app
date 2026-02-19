"""
Serializers for turfs app.
"""

from rest_framework import serializers
from .models import Turf, TurfImage, Sport, Amenity, Review, SlotOffer, OfferType
from users.serializers import CustomUserBasicSerializer
from core.utils import extract_coordinates_from_google_maps_share_link
from datetime import date
from decimal import Decimal, ROUND_HALF_UP


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
    """Serializer for turf images — returns absolute URLs."""
    image = serializers.SerializerMethodField()

    class Meta:
        model = TurfImage
        fields = ['id', 'image', 'caption', 'is_cover', 'uploaded_at']
        read_only_fields = ['id', 'uploaded_at']

    def get_image(self, obj):
        request = self.context.get('request')
        if obj.image:
            url = obj.image.url
            if request:
                return request.build_absolute_uri(url)
            return url
        return None


class TurfListSerializer(serializers.ModelSerializer):
    """Simplified turf serializer for list view."""
    sports = SportSerializer(many=True, read_only=True)
    amenities = AmenitySerializer(many=True, read_only=True)
    images = TurfImageSerializer(many=True, read_only=True)
    cover_image = serializers.SerializerMethodField()
    distance = serializers.FloatField(read_only=True, required=False)
    has_active_offer = serializers.SerializerMethodField()
    max_offer_type = serializers.SerializerMethodField()
    max_offer_value = serializers.SerializerMethodField()
    
    class Meta:
        model = Turf
        fields = [
            'id', 'name', 'city', 'price_per_hour', 'rating', 'review_count',
            'address', 'latitude', 'longitude', 'sports', 'amenities',
            'images', 'cover_image', 'distance', 'status', 'rejection_reason',
            'suspend_reason', 'google_maps_share_link',
            'has_active_offer', 'max_offer_type', 'max_offer_value',
        ]
    
    def get_cover_image(self, obj):
        """Get the cover image as an absolute URL."""
        cover = obj.images.filter(is_cover=True).first()
        if not cover and obj.images.exists():
            cover = obj.images.first()
        if cover and cover.image:
            request = self.context.get('request')
            url = cover.image.url
            if request:
                return request.build_absolute_uri(url)
            return url
        return None

    def _get_best_offer(self, turf):
        """Find the active SlotOffer that yields the maximum absolute discount."""
        today = date.today()
        offers = SlotOffer.objects.filter(
            slot_master__turf=turf,
            is_active=True,
            valid_from__lte=today,
            valid_until__gte=today,
        ).select_related('slot_master')

        best_offer = None
        best_discount = Decimal('0.00')

        for offer in offers:
            discount = offer.calculate_discount(offer.slot_master.base_price)
            if discount > best_discount:
                best_discount = discount
                best_offer = offer

        return best_offer

    def get_has_active_offer(self, turf):
        return self._get_best_offer(turf) is not None

    def get_max_offer_type(self, turf):
        offer = self._get_best_offer(turf)
        return offer.offer_type if offer else None

    def get_max_offer_value(self, turf):
        offer = self._get_best_offer(turf)
        if offer is None:
            return None
        return str(offer.value)


class TurfDetailSerializer(TurfListSerializer):
    """Detailed turf serializer — extends TurfListSerializer with additional fields."""
    owner = CustomUserBasicSerializer(read_only=True)
    reviews = serializers.SerializerMethodField()
    distance = serializers.FloatField(read_only=True, required=False)
    approved_by_name = serializers.SerializerMethodField()
    
    class Meta:
        model = Turf
        fields = [
            'id', 'owner', 'name', 'description', 'address', 'city', 'state',
            'postal_code', 'latitude', 'longitude', 'price_per_hour', 'max_players',
            'status', 'rejection_reason', 'suspend_reason', 'rating', 'review_count',
            'sports', 'amenities', 'images', 'cover_image',
            'reviews', 'is_active', 'google_maps_share_link', 'distance',
            'has_active_offer', 'max_offer_type', 'max_offer_value',
            'created_at', 'updated_at', 'approved_at', 'approved_by_name',
        ]
        read_only_fields = ['id', 'owner', 'status', 'created_at', 'updated_at', 'approved_at']

    def get_approved_by_name(self, obj):
        if obj.approved_by:
            return obj.approved_by.get_full_name() or obj.approved_by.username
        return None
    
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
        
        # Explicitly set status to pending and is_active to False for new registrations
        validated_data['status'] = 'pending'
        validated_data['is_active'] = False
        
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
