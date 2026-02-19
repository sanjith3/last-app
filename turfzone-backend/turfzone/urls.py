"""
URL configuration for turfzone project.
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.http import JsonResponse


def health_check(request):
    """Simple health check endpoint for connectivity debugging."""
    return JsonResponse({
        'status': 'ok',
        'message': 'TurfZone backend is running',
        'debug': settings.DEBUG,
    })


urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/health/', health_check),
    path('api/users/', include('users.urls')),
    path('api/turfs/', include('turfs.urls')),
    path('api/bookings/', include('bookings.urls')),
    path('api/finance/', include('finance.urls')),
    path('truff-admin/', include('truff_admin_panel.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

