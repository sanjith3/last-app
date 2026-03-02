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
    path('turfs/<int:turf_id>/edit/', views.TurfEditView.as_view(), name='turf_edit'),

    # Owner Management
    path('owners/', views.OwnerListView.as_view(), name='owners'),
    path('owners/<int:owner_id>/', views.OwnerDetailView.as_view(), name='owner_detail'),

    # Bookings
    path('bookings/', views.BookingListView.as_view(), name='bookings'),
    path('bookings/<int:booking_id>/', views.BookingDetailView.as_view(), name='booking_detail'),

    # Users (customers)
    path('users/', views.UserListView.as_view(), name='users'),
    path('users/bulk-action/', views.UserBulkActionView.as_view(), name='users_bulk_action'),
    path('users/<int:user_id>/', views.UserDetailView.as_view(), name='user_detail'),

    # Revenue
    path('revenue/', views.RevenueView.as_view(), name='revenue'),

    # PIN Change Requests
    path('pin-requests/', views.PinRequestListView.as_view(), name='pin_requests'),

    # Audit Log
    path('audit-log/', views.AuditLogView.as_view(), name='audit_log'),

    # Settings
    path('settings/', views.SettingsView.as_view(), name='settings'),

    # Promo Codes
    path('promo-codes/', views.PromoCodeListView.as_view(), name='promo_codes'),
    path('promo-codes/create/', views.PromoCodeCreateView.as_view(), name='promo_code_create'),
    path('promo-codes/<int:code_id>/edit/', views.PromoCodeEditView.as_view(), name='promo_code_edit'),
    path('promo-codes/<int:code_id>/delete/', views.PromoCodeDeleteView.as_view(), name='promo_code_delete'),

    # CSV Exports
    path('export/bookings/', views.ExportBookingsView.as_view(), name='export_bookings'),
    path('export/owners/', views.ExportOwnersView.as_view(), name='export_owners'),
    path('export/turfs/', views.ExportTurfsView.as_view(), name='export_turfs'),
    path('export/revenue/', views.ExportRevenueView.as_view(), name='export_revenue'),
    path('export/users/', views.ExportUsersView.as_view(), name='export_users'),
    path('export/audit-log/', views.ExportAuditLogView.as_view(), name='export_audit_log'),

    # Call Management
    path('calls/', views.CallListView.as_view(), name='calls'),
    path('calls/queue/', views.CallQueueView.as_view(), name='call_queue'),
    path('calls/pending/json/', views.PendingCallsJsonView.as_view(), name='pending_calls_json'),
    path('calls/<int:call_id>/', views.CallDetailView.as_view(), name='call_detail_admin'),
    path('calls/<int:call_id>/acknowledge/', views.CallAcknowledgeView.as_view(), name='call_acknowledge'),
    path('calls/<int:call_id>/connect/', views.CallConnectView.as_view(), name='call_connect'),

    # Support Chat (admin) — specific paths BEFORE wildcard <str:ticket_id>
    path('support/', views.SupportDashboardView.as_view(), name='support'),
    path('support/unread-count/', views.SupportUnreadCountView.as_view(), name='support_unread'),
    path('support/<str:ticket_id>/', views.SupportTicketDetailView.as_view(), name='support_ticket'),
]
