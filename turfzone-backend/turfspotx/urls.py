"""
URL configuration for turfspotx project.
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.http import JsonResponse
from django.views.generic import TemplateView
from pathlib import Path


def health_check(request):
    """Simple health check endpoint for connectivity debugging."""
    return JsonResponse({
        'status': 'ok',
        'message': 'TurfSpotX backend is running',
        'debug': settings.DEBUG,
    })


# Landing page only if trufspot-landing directory exists
_landing_dir = Path(settings.BASE_DIR).parent.parent / 'trufspot-landing'

urlpatterns = [
    # Landing page (bundled in templates/index.html)
    path('', TemplateView.as_view(template_name='index.html'), name='landing'),

    path('admin/', admin.site.urls),
    path('api/health/', health_check),
    path('api/users/', include('users.urls')),
    path('api/turfs/', include('turfs.urls')),
    path('api/bookings/', include('bookings.urls')),
    path('api/finance/', include('finance.urls')),
    path('api/payments/', include('payments.urls')),
    path('api/growth/', include('growth.urls')),
    path('api/support/', include('support.urls')),
    path('api/coupons/', include('users.coupon_urls')),
    path('api/whatsapp/', include('users.whatsapp_urls')),
    path('truff-admin/', include('truff_admin_panel.urls')),

    # Legal pages (served as standalone HTML)
    path('legal/terms/', TemplateView.as_view(template_name='legal/terms.html'), name='legal-terms'),
    path('legal/privacy/', TemplateView.as_view(template_name='legal/privacy.html'), name='legal-privacy'),
    path('legal/cancellation/', TemplateView.as_view(template_name='legal/cancellation.html'), name='legal-cancellation'),
    path('legal/owner-agreement/', TemplateView.as_view(template_name='legal/owner_agreement.html'), name='legal-owner-agreement'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
