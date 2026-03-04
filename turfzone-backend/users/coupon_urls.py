"""
Coupon URL configuration — /api/coupons/
"""
from django.urls import path
from .views_extended import validate_coupon, get_available_offers

urlpatterns = [
    path('validate/', validate_coupon, name='coupon-validate'),
    path('available/', get_available_offers, name='coupon-available'),
]
