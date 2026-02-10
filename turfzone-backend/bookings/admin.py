from django.contrib import admin
from .models import Booking, Payment


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'turf', 'booking_date', 'booking_status', 'payment_status', 'final_price']
    list_filter = ['booking_status', 'payment_status', 'booking_date']
    search_fields = ['user__username', 'turf__name', 'id']
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('Booking Information', {
            'fields': ('user', 'turf', 'booking_date', 'start_time', 'end_time')
        }),
        ('Pricing', {
            'fields': ('price_per_hour', 'total_price', 'discount', 'final_price')
        }),
        ('Status', {
            'fields': ('booking_status', 'payment_status')
        }),
        ('Cancellation Details', {
            'fields': ('cancelled_reason', 'cancelled_at', 'cancelled_by_admin')
        }),
        ('Additional Info', {
            'fields': ('notes', 'created_at', 'updated_at')
        }),
    )
    
    actions = ['mark_as_confirmed', 'mark_as_completed', 'cancel_bookings']
    
    def mark_as_confirmed(self, request, queryset):
        count = queryset.update(booking_status='confirmed', payment_status='paid')
        self.message_user(request, f'{count} bookings were confirmed.')
    mark_as_confirmed.short_description = 'Mark as Confirmed'
    
    def mark_as_completed(self, request, queryset):
        count = queryset.update(booking_status='completed')
        self.message_user(request, f'{count} bookings were marked as completed.')
    mark_as_completed.short_description = 'Mark as Completed'
    
    def cancel_bookings(self, request, queryset):
        count = queryset.update(booking_status='cancelled')
        self.message_user(request, f'{count} bookings were cancelled.')
    cancel_bookings.short_description = 'Cancel selected bookings'


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['transaction_id', 'booking', 'amount', 'status', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['transaction_id', 'booking__id']
    readonly_fields = ['created_at', 'updated_at']
