"""
URL configuration for users app.
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views
from .views_extended import (
    ChatViewSet, PromoCodeViewSet,
    DeviceTokenViewSet, DisputeViewSet, ReferralViewSet,
    OwnerBankDetailsViewSet, register_fcm_token as register_fcm_token_view,
    register_user, reset_password,
)

router = DefaultRouter()
router.register(r'user-registration', views.UserRegistrationViewSet, basename='user-registration')
router.register(r'user-login', views.UserLoginViewSet, basename='user-login')
router.register(r'user-profile', views.UserProfileViewSet, basename='user-profile')
router.register(r'turf-owner-profile', views.TurfOwnerProfileViewSet, basename='turf-owner-profile')

# New feature viewsets
router.register(r'chat', ChatViewSet, basename='chat')
router.register(r'promo', PromoCodeViewSet, basename='promo')
router.register(r'devices', DeviceTokenViewSet, basename='devices')
router.register(r'disputes', DisputeViewSet, basename='disputes')
router.register(r'referrals', ReferralViewSet, basename='referrals')
router.register(r'owner-bank', OwnerBankDetailsViewSet, basename='owner-bank')

urlpatterns = [
    path('', include(router.urls)),
    path('owner/approval-status/', views.owner_approval_status, name='owner-approval-status'),
    path('fcm-token/', register_fcm_token_view, name='register-fcm-token'),
    path('auth/register/', register_user, name='register-user'),
    path('auth/reset-password/', reset_password, name='reset-password'),
]
