"""
Admin registration for growth models.
"""

from django.contrib import admin
from .models import ReferralReward, WalletTransaction, UserStreak, TeamBooking


@admin.register(ReferralReward)
class ReferralRewardAdmin(admin.ModelAdmin):
    list_display = ['referrer', 'referee', 'stage', 'amount', 'status', 'created_at', 'paid_at']
    list_filter = ['stage', 'status']
    search_fields = ['referrer__username', 'referee__username']
    readonly_fields = ['created_at']


@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = ['user', 'amount', 'type', 'description', 'expires_at', 'is_expired', 'created_at']
    list_filter = ['type', 'is_expired']
    search_fields = ['user__username', 'description']
    readonly_fields = ['created_at']


@admin.register(UserStreak)
class UserStreakAdmin(admin.ModelAdmin):
    list_display = ['user', 'current_streak', 'longest_streak', 'last_booking_date', 'next_reward_at']
    search_fields = ['user__username']


@admin.register(TeamBooking)
class TeamBookingAdmin(admin.ModelAdmin):
    list_display = ['booking', 'captain', 'member', 'invite_code', 'status', 'cashback_awarded', 'created_at']
    list_filter = ['status', 'cashback_awarded']
    search_fields = ['captain__username', 'member__username', 'invite_code']
    readonly_fields = ['created_at']
