"""
URL configuration for support REST API.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import SupportViewSet

router = DefaultRouter()
router.register(r'', SupportViewSet, basename='support')

urlpatterns = [
    path('', include(router.urls)),
]
