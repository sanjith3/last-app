from django.contrib import admin
from .models import SupportTicket, SupportMessage


class SupportMessageInline(admin.TabularInline):
    model = SupportMessage
    extra = 0
    readonly_fields = ['sender', 'message', 'is_read', 'created_at']


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ['ticket_id', 'user', 'subject', 'status', 'priority', 'assigned_to', 'updated_at']
    list_filter = ['status', 'priority']
    search_fields = ['ticket_id', 'user__username', 'subject']
    inlines = [SupportMessageInline]
    readonly_fields = ['ticket_id', 'created_at', 'updated_at', 'resolved_at']


@admin.register(SupportMessage)
class SupportMessageAdmin(admin.ModelAdmin):
    list_display = ['ticket', 'sender', 'is_read', 'created_at']
    list_filter = ['is_read']
    readonly_fields = ['created_at']
