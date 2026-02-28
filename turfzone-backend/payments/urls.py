"""
URL configuration for payments app (Razorpay integration).
"""
from django.urls import path
from . import views

urlpatterns = [
    path('create-order/', views.create_order, name='payment-create-order'),
    path('verify/', views.verify_payment, name='payment-verify'),
    path('webhook/', views.payment_webhook, name='payment-webhook'),
]
