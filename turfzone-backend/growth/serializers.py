"""
Serializers for growth app — referral rewards, wallet, streaks, captain mode.
"""

from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import ReferralReward, WalletTransaction, UserStreak, TeamBooking

User = get_user_model()


class ReferralRewardSerializer(serializers.ModelSerializer):
    referee_name = serializers.SerializerMethodField()

    class Meta:
        model = ReferralReward
        fields = ['id', 'referee', 'referee_name', 'stage', 'amount', 'status', 'created_at', 'paid_at']
        read_only_fields = ['id', 'created_at']

    def get_referee_name(self, obj):
        return obj.referee.first_name or obj.referee.username


class WalletTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WalletTransaction
        fields = ['id', 'amount', 'type', 'description', 'expires_at', 'is_expired', 'created_at']
        read_only_fields = ['id', 'created_at']


class UserStreakSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserStreak
        fields = ['current_streak', 'longest_streak', 'last_booking_date', 'next_reward_at']


class ReferredFriendSerializer(serializers.Serializer):
    """Serializer for the referred friends list."""
    id = serializers.IntegerField()
    name = serializers.CharField()
    status = serializers.CharField()  # 'installed' or 'booked'
    joined_at = serializers.DateTimeField()
    cashback_earned = serializers.DecimalField(max_digits=10, decimal_places=2)


class TeamBookingSerializer(serializers.ModelSerializer):
    member_name = serializers.SerializerMethodField()

    class Meta:
        model = TeamBooking
        fields = ['id', 'booking', 'invite_code', 'member', 'member_name', 'status',
                  'cashback_awarded', 'created_at', 'joined_at']
        read_only_fields = ['id', 'created_at']

    def get_member_name(self, obj):
        if obj.member:
            return obj.member.first_name or obj.member.username
        return None
