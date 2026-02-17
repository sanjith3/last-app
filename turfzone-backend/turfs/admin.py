from django.contrib import admin
from .models import Turf, TurfImage, Sport, Amenity, Review, SlotMaster, SlotOffer, BlockedSlot


@admin.register(Sport)
class SportAdmin(admin.ModelAdmin):
    list_display = ['name', 'description']
    search_fields = ['name']


@admin.register(Amenity)
class AmenityAdmin(admin.ModelAdmin):
    list_display = ['name', 'description']
    search_fields = ['name']


class TurfImageInline(admin.TabularInline):
    model = TurfImage
    extra = 1


@admin.register(Turf)
class TurfAdmin(admin.ModelAdmin):
    list_display = ['name', 'city', 'owner', 'status', 'rating', 'price_per_hour', 'created_at']
    list_filter = ['status', 'city', 'state', 'rating', 'created_at']
    search_fields = ['name', 'address', 'city', 'owner__username']
    readonly_fields = ['created_at', 'updated_at', 'approved_at']
    filter_horizontal = ['sports', 'amenities']
    inlines = [TurfImageInline]
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('owner', 'name', 'description', 'status')
        }),
        ('Location', {
            'fields': ('address', 'city', 'state', 'postal_code', 'latitude', 'longitude', 'google_maps_share_link')
        }),
        ('Details', {
            'fields': ('price_per_hour', 'max_players', 'sports', 'amenities')
        }),
        ('Ratings & Reviews', {
            'fields': ('rating', 'review_count')
        }),
        ('Metadata', {
            'fields': ('is_active', 'created_at', 'updated_at', 'approved_at')
        }),
    )
    
    actions = ['approve_turfs', 'reject_turfs']
    
    def approve_turfs(self, request, queryset):
        count = 0
        for turf in queryset:
            turf.approve()
            count += 1
        self.message_user(request, f'{count} turfs were approved.')
    approve_turfs.short_description = 'Approve selected turfs'
    
    def reject_turfs(self, request, queryset):
        count = 0
        for turf in queryset:
            turf.reject()
            count += 1
        self.message_user(request, f'{count} turfs were rejected.')
    reject_turfs.short_description = 'Reject selected turfs'


@admin.register(TurfImage)
class TurfImageAdmin(admin.ModelAdmin):
    list_display = ['turf', 'is_cover', 'uploaded_at']
    list_filter = ['is_cover', 'uploaded_at']
    search_fields = ['turf__name']


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ['turf', 'user', 'rating', 'created_at']
    list_filter = ['rating', 'created_at']
    search_fields = ['turf__name', 'user__username']
    readonly_fields = ['created_at', 'updated_at']


# ---------------------------------------------------------------------------
# Slot & Offer Admin
# ---------------------------------------------------------------------------

class SlotOfferInline(admin.TabularInline):
    model = SlotOffer
    extra = 0
    fields = ['offer_type', 'value', 'max_discount_cap', 'valid_from', 'valid_until', 'is_active']


@admin.register(SlotMaster)
class SlotMasterAdmin(admin.ModelAdmin):
    list_display = ['turf', 'get_day_display', 'start_time', 'end_time', 'base_price', 'is_active']
    list_filter = ['turf', 'day_of_week', 'is_active']
    search_fields = ['turf__name']
    inlines = [SlotOfferInline]

    def get_day_display(self, obj):
        return obj.get_day_of_week_display()
    get_day_display.short_description = 'Day'


@admin.register(SlotOffer)
class SlotOfferAdmin(admin.ModelAdmin):
    list_display = ['slot_master', 'offer_type', 'value', 'max_discount_cap', 'valid_from', 'valid_until', 'is_active']
    list_filter = ['offer_type', 'is_active', 'valid_from']
    search_fields = ['slot_master__turf__name']


@admin.register(BlockedSlot)
class BlockedSlotAdmin(admin.ModelAdmin):
    list_display = ['turf', 'slot_master', 'date', 'reason']
    list_filter = ['date', 'turf']
    search_fields = ['turf__name', 'reason']

