"""
Finance app URL configuration.
All endpoints require IsPlatformAdmin (role == 'admin').
"""

from django.urls import path
from .views import FinanceDashboardView, GSTReportView, CommissionReportView

urlpatterns = [
    path('dashboard/', FinanceDashboardView.as_view(), name='finance-dashboard'),
    path('gst-report/', GSTReportView.as_view(), name='finance-gst-report'),
    path('commission-report/', CommissionReportView.as_view(), name='finance-commission-report'),
]
