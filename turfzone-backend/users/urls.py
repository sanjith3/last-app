"""
URL configuration for users app.
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views
from .views_extended import (
    OTPViewSet, ChatViewSet, PromoCodeViewSet,
    DeviceTokenViewSet, DisputeViewSet, ReferralViewSet,
    OwnerBankDetailsViewSet, register_fcm_token as register_fcm_token_view,
)

router = DefaultRouter()
router.register(r'user-registration', views.UserRegistrationViewSet, basename='user-registration')
router.register(r'user-login', views.UserLoginViewSet, basename='user-login')
router.register(r'user-profile', views.UserProfileViewSet, basename='user-profile')
router.register(r'turf-owner-profile', views.TurfOwnerProfileViewSet, basename='turf-owner-profile')

# New feature viewsets
router.register(r'otp', OTPViewSet, basename='otp')
router.register(r'chat', ChatViewSet, basename='chat')
router.register(r'promo', PromoCodeViewSet, basename='promo')
router.register(r'devices', DeviceTokenViewSet, basename='devices')
router.register(r'disputes', DisputeViewSet, basename='disputes')
router.register(r'referrals', ReferralViewSet, basename='referrals')
router.register(r'owner-bank', OwnerBankDetailsViewSet, basename='owner-bank')

urlpatterns = [
    path('', include(router.urls)),
    path('fcm-token/', register_fcm_token_view, name='register-fcm-token'),
]
