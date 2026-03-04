"""
URL configuration for growth app.
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'referral', views.ReferralViewSet, basename='referral')
router.register(r'wallet', views.WalletViewSet, basename='wallet')
router.register(r'streak-loyalty', views.StreakLoyaltyViewSet, basename='streak-loyalty')
router.register(r'live-stats', views.LiveStatsViewSet, basename='live-stats')
router.register(r'captain', views.CaptainViewSet, basename='captain')
router.register(r'owner-qr', views.OwnerQRViewSet, basename='owner-qr')

urlpatterns = [
    path('', include(router.urls)),
    # Flutter offer config endpoint
    path('config/', views.OfferConfigAPIView.as_view(), name='offer-config'),
]
