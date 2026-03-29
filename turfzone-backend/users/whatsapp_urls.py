"""
URL configuration for WhatsApp OTP endpoints.
Included from project urls.py under 'api/whatsapp/'.
"""

from django.urls import path
from .views_extended import send_whatsapp_otp, verify_whatsapp_otp, whatsapp_webhook

urlpatterns = [
    path('send-otp/', send_whatsapp_otp, name='whatsapp-send-otp'),
    path('verify-otp/', verify_whatsapp_otp, name='whatsapp-verify-otp'),
    path('webhook/', whatsapp_webhook, name='whatsapp-webhook'),
]
