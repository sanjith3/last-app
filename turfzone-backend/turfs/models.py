"""
Turf models for TurfZone backend.
"""

from django.db import models
from django.contrib.auth import get_user_model
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone

User = get_user_model()


class TurfStatus(models.TextChoices):
    PENDING = 'pending', 'Pending Approval'
    APPROVED = 'approved', 'Approved'
    REJECTED = 'rejected', 'Rejected'
    SUSPENDED = 'suspended', 'Suspended'


class Sport(models.Model):
    """Available sports at turfs."""
    name = models.CharField(max_length=50, unique=True)
    icon = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    
    def __str__(self):
        return self.name
    
    class Meta:
        ordering = ['name']


class Amenity(models.Model):
    """Available amenities at turfs."""
    name = models.CharField(max_length=100, unique=True)
    icon = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    
    def __str__(self):
        return self.name
    
    class Meta:
        ordering = ['name']
        verbose_name_plural = 'Amenities'


class Turf(models.Model):
    """Main Turf model."""
    
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='turfs')
    
    # Basic Information
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, null=True)
    address = models.TextField()
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=10, blank=True, null=True)
    
    # Location (from Google Maps)
    latitude = models.FloatField()
    longitude = models.FloatField()
    
    # Pricing and Details
    price_per_hour = models.IntegerField(validators=[MinValueValidator(1)])
    max_players = models.IntegerField(default=22, validators=[MinValueValidator(1)])
    
    # Status
    status = models.CharField(max_length=20, choices=TurfStatus.choices, default=TurfStatus.PENDING)
    rejection_reason = models.TextField(blank=True, null=True)
    
    # Relationships
    sports = models.ManyToManyField(Sport, related_name='turfs')
    amenities = models.ManyToManyField(Amenity, related_name='turfs')
    
    # Ratings and Reviews
    rating = models.FloatField(
        default=4.5,
        validators=[MinValueValidator(0.0), MaxValueValidator(5.0)]
    )
    review_count = models.IntegerField(default=0)
    
    # Metadata
    is_active = models.BooleanField(default=False)
    google_maps_share_link = models.URLField(blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    approved_at = models.DateTimeField(blank=True, null=True)
    
    def __str__(self):
        return f"{self.name} ({self.city})"
    
    def approve(self):
        """Approve this turf listing."""
        self.status = TurfStatus.APPROVED
        self.is_active = True
        self.approved_at = timezone.now()
        self.rejection_reason = None
        self.save()
        
        # Verify the owner and upgrade role when their first turf is approved
        if self.owner.role == 'user':
            self.owner.role = 'turf_owner'
            self.owner.is_verified = True
            self.owner.save()
        elif not self.owner.is_verified:
            self.owner.is_verified = True
            self.owner.save()
    
    def reject(self, reason=None):
        """Reject this turf listing."""
        self.status = TurfStatus.REJECTED
        if reason:
            self.rejection_reason = reason
        self.save()
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'is_active']),
            models.Index(fields=['latitude', 'longitude']),
            models.Index(fields=['owner', 'status']),
        ]


class TurfImage(models.Model):
    """Images for turfs."""
    turf = models.ForeignKey(Turf, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='turf_images/')
    caption = models.CharField(max_length=255, blank=True, null=True)
    is_cover = models.BooleanField(default=False)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"Image: {self.turf.name}"
    
    class Meta:
        ordering = ['-is_cover', '-uploaded_at']


class Review(models.Model):
    """Reviews for turfs."""
    turf = models.ForeignKey(Turf, on_delete=models.CASCADE, related_name='reviews')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    rating = models.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    comment = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"Review: {self.turf.name} - {self.rating}★"
    
    class Meta:
        ordering = ['-created_at']
        unique_together = ['turf', 'user']  # One review per user per turf
