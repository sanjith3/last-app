"""
Truff-Admin URL routes — all under /truff-admin/
"""

from django.urls import path
from . import views

app_name = 'truff_admin'

urlpatterns = [
    # Auth
    path('login/', views.AdminLoginView.as_view(), name='login'),
    path('logout/', views.AdminLogoutView.as_view(), name='logout'),

    # Dashboard
    path('', views.DashboardView.as_view(), name='dashboard'),

    # Turf Management
    path('turfs/', views.TurfListView.as_view(), name='turfs'),
    path('turfs/pending/', views.TurfPendingListView.as_view(), name='turfs_pending'),
    path('turfs/bulk-action/', views.TurfBulkActionView.as_view(), name='turfs_bulk_action'),
    path('turfs/<int:turf_id>/', views.TurfDetailView.as_view(), name='turf_detail'),
    path('turfs/<int:turf_id>/action/', views.TurfActionView.as_view(), name='turf_action'),

    # Owner Management
    path('owners/', views.OwnerListView.as_view(), name='owners'),
    path('owners/<int:owner_id>/', views.OwnerDetailView.as_view(), name='owner_detail'),

    # Bookings
    path('bookings/', views.BookingListView.as_view(), name='bookings'),
    path('bookings/<int:booking_id>/', views.BookingDetailView.as_view(), name='booking_detail'),

    # Users (customers)
    path('users/', views.UserListView.as_view(), name='users'),
    path('users/<int:user_id>/', views.UserDetailView.as_view(), name='user_detail'),

    # Revenue
    path('revenue/', views.RevenueView.as_view(), name='revenue'),

    # PIN Change Requests
    path('pin-requests/', views.PinRequestListView.as_view(), name='pin_requests'),

    # Audit Log
    path('audit-log/', views.AuditLogView.as_view(), name='audit_log'),

    # Settings
    path('settings/', views.SettingsView.as_view(), name='settings'),

    # CSV Exports
    path('export/bookings/', views.ExportBookingsView.as_view(), name='export_bookings'),
    path('export/owners/', views.ExportOwnersView.as_view(), name='export_owners'),
    path('export/turfs/', views.ExportTurfsView.as_view(), name='export_turfs'),
    path('export/revenue/', views.ExportRevenueView.as_view(), name='export_revenue'),
    path('export/users/', views.ExportUsersView.as_view(), name='export_users'),

    # Call Management
    path('calls/', views.CallListView.as_view(), name='calls'),
    path('calls/queue/', views.CallQueueView.as_view(), name='call_queue'),
    path('calls/pending/json/', views.PendingCallsJsonView.as_view(), name='pending_calls_json'),
    path('calls/<int:call_id>/', views.CallDetailView.as_view(), name='call_detail_admin'),
    path('calls/<int:call_id>/acknowledge/', views.CallAcknowledgeView.as_view(), name='call_acknowledge'),
    path('calls/<int:call_id>/connect/', views.CallConnectView.as_view(), name='call_connect'),
]
