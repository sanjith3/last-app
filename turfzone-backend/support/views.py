"""
REST API views for User Support Chat.

Endpoints (all under /api/support/):
  GET  /tickets/              - list user's tickets
  POST /tickets/              - create new ticket + first message
  GET  /tickets/{ticket_id}/  - ticket detail + messages
  POST /tickets/{ticket_id}/messages/ - send message (polling)
  GET  /tickets/{ticket_id}/messages/?after=ISO - new messages since timestamp
"""
import logging
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import SupportTicket, SupportMessage
from .serializers import (
    SupportTicketListSerializer,
    SupportTicketDetailSerializer,
    SupportMessageSerializer,
)

logger = logging.getLogger(__name__)


class SupportViewSet(viewsets.ViewSet):
    """User-facing support ticket and messaging endpoints."""

    permission_classes = [IsAuthenticated]

    # ------------------------------------------------------------------
    # GET /api/support/tickets/
    # ------------------------------------------------------------------
    @action(detail=False, methods=['get'], url_path='tickets')
    def list_tickets(self, request):
        """List the authenticated user's support tickets."""
        qs = SupportTicket.objects.filter(user=request.user).order_by('-updated_at')
        status_filter = request.query_params.get('status', '')
        if status_filter:
            qs = qs.filter(status=status_filter)
        serializer = SupportTicketListSerializer(qs[:50], many=True, context={'request': request})
        return Response({'success': True, 'tickets': serializer.data})

    # ------------------------------------------------------------------
    # POST /api/support/tickets/create/
    # ------------------------------------------------------------------
    @action(detail=False, methods=['post'], url_path='tickets/create')
    def create_ticket(self, request):
        """Create a new ticket with an initial message."""
        subject = request.data.get('subject', '').strip()
        message_text = request.data.get('message', '').strip()

        if not subject:
            return Response({'error': 'subject is required'}, status=status.HTTP_400_BAD_REQUEST)
        if not message_text:
            return Response({'error': 'message is required'}, status=status.HTTP_400_BAD_REQUEST)

        ticket = SupportTicket.objects.create(
            user=request.user,
            subject=subject,
            status='open',
            priority=request.data.get('priority', 'medium'),
        )
        SupportMessage.objects.create(
            ticket=ticket,
            sender=request.user,
            message=message_text,
        )
        logger.info('[SUPPORT] New ticket %s created by user %s', ticket.ticket_id, request.user.id)
        return Response({
            'success': True,
            'ticket_id': ticket.ticket_id,
            'message': 'Support ticket created. Our team will respond shortly.',
        }, status=status.HTTP_201_CREATED)

    # ------------------------------------------------------------------
    # GET /api/support/tickets/{ticket_id}/
    # ------------------------------------------------------------------
    @action(detail=False, methods=['get'], url_path=r'tickets/(?P<ticket_id>TKT-[\w-]+)')
    def get_ticket(self, request, ticket_id=None):
        """Get a ticket with full message history. Marks admin replies as read."""
        try:
            ticket = SupportTicket.objects.get(ticket_id=ticket_id, user=request.user)
        except SupportTicket.DoesNotExist:
            return Response({'error': 'Ticket not found'}, status=status.HTTP_404_NOT_FOUND)

        # Mark admin replies as read
        ticket.messages.filter(is_read=False).exclude(sender=request.user).update(is_read=True)

        serializer = SupportTicketDetailSerializer(ticket, context={'request': request})
        return Response({'success': True, 'ticket': serializer.data})

    # ------------------------------------------------------------------
    # POST /api/support/tickets/{ticket_id}/messages/
    # GET  /api/support/tickets/{ticket_id}/messages/?after=ISO
    # ------------------------------------------------------------------
    @action(detail=False, methods=['post', 'get'], url_path=r'tickets/(?P<ticket_id>TKT-[\w-]+)/messages')
    def messages(self, request, ticket_id=None):
        """Send or poll messages for a ticket."""
        try:
            ticket = SupportTicket.objects.get(ticket_id=ticket_id, user=request.user)
        except SupportTicket.DoesNotExist:
            return Response({'error': 'Ticket not found'}, status=status.HTTP_404_NOT_FOUND)

        if request.method == 'POST':
            text = request.data.get('message', '').strip()
            if not text:
                return Response({'error': 'message is required'}, status=status.HTTP_400_BAD_REQUEST)

            msg = SupportMessage.objects.create(
                ticket=ticket,
                sender=request.user,
                message=text,
            )
            # Reopen if resolved/closed
            if ticket.status in ('resolved', 'closed'):
                ticket.status = 'open'
                ticket.save(update_fields=['status'])

            try:
                from truff_admin_panel.push_models import AdminNotification
                AdminNotification.objects.create(
                    title=f"New Message: {ticket.ticket_id}",
                    body=f"{request.user.username}: {text[:50]}{'...' if len(text)>50 else ''}",
                    notification_type='support_alert',
                    target_type='all',  # You might want to target specific admins in the future
                ).send()
            except Exception as e:
                logger.error(f"Failed to send admin push for ticket {ticket.ticket_id}: {e}")

            serializer = SupportMessageSerializer(msg, context={'request': request})
            return Response({'success': True, 'message': serializer.data}, status=status.HTTP_201_CREATED)

        # GET — polling for new messages
        after = request.query_params.get('after')
        qs = ticket.messages.all()
        if after:
            try:
                qs = qs.filter(created_at__gt=after)
            except Exception:
                pass
        # Mark new admin replies as read
        qs.filter(is_read=False).exclude(sender=request.user).update(is_read=True)

        serializer = SupportMessageSerializer(qs, many=True, context={'request': request})
        return Response({'success': True, 'messages': serializer.data})
