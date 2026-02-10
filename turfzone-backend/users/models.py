"""
User models for TurfZone backend.
"""

from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone


class CustomUser(AbstractUser):
    """Custom user model with role-based access."""
    
    class UserRole(models.TextChoices):
        USER = 'user', 'Normal User'
        TURF_OWNER = 'turf_owner', 'Turf Owner'
        PLATFORM_ADMIN = 'admin', 'Platform Admin'
    
    phone_number = models.CharField(max_length=15, blank=True, null=True)
    role = models.CharField(max_length=20, choices=UserRole.choices, default=UserRole.USER)
    profile_picture = models.ImageField(upload_to='profile_pictures/', blank=True, null=True)
    bio = models.TextField(blank=True, null=True)
    is_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"
    
    class Meta:
        ordering = ['-created_at']


class TurfOwner(models.Model):
    """Extended profile for turf owners."""
    
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='turf_owner_profile')
    bank_account = models.CharField(max_length=50, blank=True, null=True)
    ifsc_code = models.CharField(max_length=11, blank=True, null=True)
    bank_name = models.CharField(max_length=100, blank=True, null=True)
    account_holder_name = models.CharField(max_length=100, blank=True, null=True)
    gst_number = models.CharField(max_length=15, blank=True, null=True)
    total_turfs = models.IntegerField(default=0)
    total_bookings = models.IntegerField(default=0)
    total_revenue = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    rating = models.FloatField(
        default=4.5,
        validators=[MinValueValidator(0.0), MaxValueValidator(5.0)]
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"TurfOwner: {self.user.username}"
    
    class Meta:
        ordering = ['-created_at']
