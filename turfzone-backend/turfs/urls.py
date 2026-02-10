"""
URL configuration for turfs app.
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'sports', views.SportViewSet, basename='sport')
router.register(r'amenities', views.AmenityViewSet, basename='amenity')
router.register(r'turfs', views.TurfViewSet, basename='turf')

urlpatterns = [
    path('', include(router.urls)),
]
