"""
Finance admin configuration.
"""

from django.contrib import admin
from .models import LedgerEntry, OwnerSettlement


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ['booking', 'entry_type', 'account', 'amount', 'created_at']
    list_filter = ['entry_type', 'account', 'created_at']
    search_fields = ['booking__id', 'description']
    readonly_fields = ['booking', 'entry_type', 'account', 'amount', 'description', 'created_at']

    def has_add_permission(self, request):
        return False  # Ledger entries are created programmatically only

    def has_delete_permission(self, request, obj=None):
        return False  # Never delete ledger entries


@admin.register(OwnerSettlement)
class OwnerSettlementAdmin(admin.ModelAdmin):
    list_display = ['owner', 'period_start', 'period_end', 'total_gross', 'net_payout', 'status', 'settled_at']
    list_filter = ['status', 'created_at']
    search_fields = ['owner__username', 'razorpay_transfer_id']
