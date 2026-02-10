from django.contrib import admin
from .models import CustomUser, TurfOwner


@admin.register(CustomUser)
class CustomUserAdmin(admin.ModelAdmin):
    list_display = ['username', 'email', 'role', 'is_verified', 'created_at']
    list_filter = ['role', 'is_verified', 'created_at']
    search_fields = ['username', 'email', 'phone_number']
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Personal Info', {'fields': ('first_name', 'last_name', 'email', 'phone_number', 'bio')}),
        ('Permissions', {
            'fields': ('is_staff', 'is_superuser', 'is_active', 'groups', 'user_permissions'),
        }),
        ('Roles & Verification', {'fields': ('role', 'is_verified')}),
        ('Profile', {'fields': ('profile_picture',)}),
        ('Important Dates', {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'email', 'password1', 'password2'),
        }),
    )


@admin.register(TurfOwner)
class TurfOwnerAdmin(admin.ModelAdmin):
    list_display = ['get_username', 'total_turfs', 'total_bookings', 'rating', 'created_at']
    list_filter = ['rating', 'created_at']
    search_fields = ['user__username', 'user__email']
    readonly_fields = ['created_at', 'updated_at']
    
    def get_username(self, obj):
        return obj.user.username
    get_username.short_description = 'Username'
