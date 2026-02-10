"""
URL configuration for users app.
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'user-registration', views.UserRegistrationViewSet, basename='user-registration')
router.register(r'user-login', views.UserLoginViewSet, basename='user-login')
router.register(r'user-profile', views.UserProfileViewSet, basename='user-profile')
router.register(r'turf-owner-profile', views.TurfOwnerProfileViewSet, basename='turf-owner-profile')

urlpatterns = [
    path('', include(router.urls)),
]
