"""
Razorpay Payment Integration Views.

Endpoints:
  POST /api/payments/create-order/ — Create a Razorpay order from preview token
  POST /api/payments/verify/        — Verify payment and confirm booking
  POST /api/payments/webhook/       — Handle Razorpay webhook events
"""

import json
import logging
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework import status

from bookings.models import Booking, BookingPreview, PaymentStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Razorpay client — lazy initialization
# ---------------------------------------------------------------------------
_razorpay_client = None


def _get_razorpay_client():
    """Lazy-load Razorpay client. Returns None if credentials not configured."""
    global _razorpay_client
    if _razorpay_client is not None:
        return _razorpay_client

    key_id = getattr(settings, 'RAZORPAY_KEY_ID', None)
    key_secret = getattr(settings, 'RAZORPAY_KEY_SECRET', None)

    if not key_id or not key_secret:
        logger.warning("Razorpay credentials not configured. Payment features disabled.")
        return None

    try:
        import razorpay
        _razorpay_client = razorpay.Client(auth=(key_id, key_secret))
        return _razorpay_client
    except ImportError:
        logger.error("razorpay package not installed. Run: pip install razorpay")
        return None


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_order(request):
    """
    Create a Razorpay order from a booking preview token.

    POST /api/payments/create-order/
    Body: {"preview_token": "uuid-here"}

    Returns: {"order_id": "...", "amount": 118000, "currency": "INR", "key_id": "rzp_test_..."}
    """
    client = _get_razorpay_client()
    if client is None:
        # ── No Razorpay keys — return a mock order so Flutter can use fallback confirm ──
        # Flutter PaymentScreen already handles statusCode == 503 with _fallbackDirectConfirm.
        # Returning 503 still triggers that path; this is fine for development.
        # If you have test keys, set RAZORPAY_KEY_ID & RAZORPAY_KEY_SECRET in your environment.
        logger.warning("Razorpay not configured — returning 503 so Flutter falls back to direct confirm.")
        return Response(
            {'error': 'Payment gateway not configured. Using direct confirmation fallback.'},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


    preview_token = request.data.get('preview_token')
    if not preview_token:
        return Response({'error': 'preview_token is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        preview = BookingPreview.objects.get(preview_token=preview_token, is_used=False)
    except BookingPreview.DoesNotExist:
        return Response({'error': 'Invalid or expired preview token'}, status=status.HTTP_400_BAD_REQUEST)

    # Check expiry
    if preview.is_expired:
        return Response({'error': 'Preview token has expired. Please create a new preview.'}, status=status.HTTP_400_BAD_REQUEST)

    # Check ownership
    if preview.user != request.user:
        return Response({'error': 'Unauthorized'}, status=status.HTTP_403_FORBIDDEN)

    # Amount in paise (smallest currency unit)
    amount_paise = int(preview.total_payable * 100)

    try:
        order_data = {
            'amount': amount_paise,
            'currency': 'INR',
            'receipt': f"preview_{str(preview.preview_token)[:8]}",
            'payment_capture': 1,  # Auto-capture on successful payment
            'notes': {
                'preview_token': str(preview.preview_token),
                'user_id': str(request.user.id),
                'turf_id': str(preview.turf_id),
                'booking_date': str(preview.booking_date),
            }
        }
        order = client.order.create(data=order_data)
    except Exception as e:
        logger.error(f"Razorpay order creation failed: {e}")
        return Response(
            {'error': 'Failed to create payment order. Please try again.'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return Response({
        'order_id': order['id'],
        'amount': order['amount'],
        'currency': order['currency'],
        'key_id': settings.RAZORPAY_KEY_ID,
        'preview_token': str(preview.preview_token),
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def verify_payment(request):
    """
    Verify Razorpay payment signature and confirm booking.

    POST /api/payments/verify/
    Body: {
      "razorpay_order_id": "order_...",
      "razorpay_payment_id": "pay_...",
      "razorpay_signature": "...",
      "preview_token": "uuid-here",
      "total_payable": "1180.00"
    }
    """
    client = _get_razorpay_client()
    if client is None:
        return Response(
            {'error': 'Payment gateway not configured'},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    razorpay_order_id = request.data.get('razorpay_order_id')
    razorpay_payment_id = request.data.get('razorpay_payment_id')
    razorpay_signature = request.data.get('razorpay_signature')
    preview_token = request.data.get('preview_token')
    total_payable = request.data.get('total_payable')

    if not all([razorpay_order_id, razorpay_payment_id, razorpay_signature, preview_token]):
        return Response(
            {'error': 'Missing required fields: razorpay_order_id, razorpay_payment_id, razorpay_signature, preview_token'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Verify signature
    params_dict = {
        'razorpay_order_id': razorpay_order_id,
        'razorpay_payment_id': razorpay_payment_id,
        'razorpay_signature': razorpay_signature,
    }

    try:
        client.utility.verify_payment_signature(params_dict)
    except Exception:
        logger.warning(f"Invalid Razorpay signature for order {razorpay_order_id}")
        return Response({'error': 'Payment verification failed. Invalid signature.'}, status=status.HTTP_400_BAD_REQUEST)

    # Find the preview
    try:
        preview = BookingPreview.objects.get(preview_token=preview_token, is_used=False)
    except BookingPreview.DoesNotExist:
        return Response({'error': 'Invalid or already used preview token'}, status=status.HTTP_400_BAD_REQUEST)

    # Now confirm via the shared service (no TestRequestFactory hack)
    from bookings.services import confirm_booking
    from decimal import Decimal

    result = confirm_booking(
        user=request.user,
        preview_token=str(preview_token),
        client_total=Decimal(str(total_payable or preview.total_payable)),
        coupon_code='',
        razorpay_order_id=razorpay_order_id,
        razorpay_payment_id=razorpay_payment_id,
        razorpay_signature=razorpay_signature,
    )

    if result.get('success'):
        return Response({
            'success': True,
            'booking_id': result.get('booking_id'),
            'payment_id': razorpay_payment_id,
            'message': 'Payment verified and booking confirmed!',
        })
    else:
        http_status = result.pop('status_code', 400)
        return Response(result, status=http_status)


@csrf_exempt
@api_view(['POST'])
@permission_classes([AllowAny])
def payment_webhook(request):
    """
    Handle Razorpay webhook events (payment.failed, refund.processed, etc.)

    POST /api/payments/webhook/
    Razorpay sends webhook body with X-Razorpay-Signature header.
    """
    webhook_secret = getattr(settings, 'RAZORPAY_WEBHOOK_SECRET', None)
    webhook_signature = request.headers.get('X-Razorpay-Signature', '')

    client = _get_razorpay_client()
    if client is None or not webhook_secret:
        # BUG-C FIX: Return 503 so Razorpay retries until keys are configured.
        # Previously returned 200 which silently discarded all webhook events.
        logger.warning("Razorpay webhook received but client/secret not configured — returning 503.")
        return Response(
            {'status': 'webhooks not configured'},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    # Verify webhook signature
    webhook_body = request.body.decode('utf-8') if isinstance(request.body, bytes) else request.body

    try:
        client.utility.verify_webhook_signature(webhook_body, webhook_signature, webhook_secret)
    except Exception:
        logger.warning("Invalid Razorpay webhook signature")
        return Response({'error': 'Invalid webhook signature'}, status=status.HTTP_400_BAD_REQUEST)

    data = request.data
    event = data.get('event', '')

    logger.info(f"Razorpay webhook received: {event}")

    if event == 'payment.failed':
        try:
            payment_entity = data['payload']['payment']['entity']
            order_id = payment_entity.get('order_id')

            if order_id:
                booking = Booking.objects.filter(razorpay_order_id=order_id).first()
                if booking:
                    booking.payment_status = PaymentStatus.FAILED
                    booking.save(update_fields=['payment_status'])
                    logger.info(f"Booking #{booking.id} payment failed for order {order_id}")
        except (KeyError, Exception) as e:
            logger.error(f"Error processing payment.failed webhook: {e}")

    elif event == 'payment.captured':
        try:
            payment_entity = data['payload']['payment']['entity']
            order_id = payment_entity.get('order_id')
            payment_id = payment_entity.get('id')

            if order_id:
                booking = Booking.objects.filter(razorpay_order_id=order_id).first()
                if booking and booking.payment_status != PaymentStatus.PAID:
                    booking.payment_status = PaymentStatus.PAID
                    booking.razorpay_payment_id = payment_id
                    booking.save(update_fields=['payment_status', 'razorpay_payment_id'])
                    logger.info(f"Booking #{booking.id} payment captured for order {order_id}")
        except (KeyError, Exception) as e:
            logger.error(f"Error processing payment.captured webhook: {e}")

    elif event == 'refund.processed':
        try:
            refund_entity = data['payload']['refund']['entity']
            payment_id = refund_entity.get('payment_id')

            if payment_id:
                booking = Booking.objects.filter(razorpay_payment_id=payment_id).first()
                if booking:
                    booking.payment_status = PaymentStatus.REFUNDED
                    booking.save(update_fields=['payment_status'])
                    logger.info(f"Booking #{booking.id} refund processed for payment {payment_id}")
        except (KeyError, Exception) as e:
            logger.error(f"Error processing refund.processed webhook: {e}")

    return Response({'status': 'ok'})
