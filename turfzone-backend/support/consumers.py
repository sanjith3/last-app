import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.tokens import AccessToken
import logging

logger = logging.getLogger(__name__)

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.ticket_id = self.scope['url_route']['kwargs']['ticket_id']
        self.room_group_name = f'chat_{self.ticket_id}'
        
        # Authenticate user
        user = await self.get_user()
        if not user or user.is_anonymous:
            await self.close()
            return
        
        self.user = user
        
        # Join room group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        
        await self.accept()
    
    async def disconnect(self, close_code):
        # Leave room group
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
    
    async def receive(self, text_data):
        text_data_json = json.loads(text_data)
        action = text_data_json.get('action')
        
        if action == 'send_message':
            message = text_data_json['message']
            
            # Save to database
            msg = await self.save_message(message)
            
            # Send to room group
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'chat_message',
                    'message': msg['message'],
                    'sender_name': msg['sender_name'],
                    'is_admin_reply': msg['is_admin_reply'],
                    'id': msg['id'],
                    'created_at': msg['created_at'],
                    'is_read': msg['is_read'],
                }
            )
            
            # Fire push notification in async background using database_sync_to_async
            await self.async_trigger_push_notifications(message)
            
    async def chat_message(self, event):
        # Send message to WebSocket exactly matching HTTP serializer payload
        await self.send(text_data=json.dumps({
            'id': event['id'],
            'message': event['message'],
            'sender_name': event['sender_name'],
            'is_admin_reply': event['is_admin_reply'],
            'created_at': event['created_at'],
            'is_read': event['is_read'],
        }))
    
    @database_sync_to_async
    def get_user(self):
        # Extract user from token in query string
        query_string = self.scope['query_string'].decode()
        token = self.extract_token(query_string)
        
        if not token:
            return AnonymousUser()
        
        try:
            access_token = AccessToken(token)
            user_id = access_token['user_id']
            from users.models import CustomUser
            return CustomUser.objects.get(id=user_id)
        except Exception as e:
            logger.error(f"WebSocket token auth failed: {e}")
            return AnonymousUser()
    
    def extract_token(self, query_string):
        for param in query_string.split('&'):
            if param.startswith('token='):
                return param[6:]
        return None
    
    @database_sync_to_async
    def save_message(self, message_text):
        from support.models import SupportTicket, SupportMessage
        ticket = SupportTicket.objects.get(ticket_id=self.ticket_id)
        msg = SupportMessage.objects.create(
            ticket=ticket,
            sender=self.user,
            message=message_text
        )
        
        # Update ticket status
        if self.user != ticket.user:
            ticket.status = 'awaiting_reply'
        else:
            ticket.status = 'open' # Admin needs to see it is open
        ticket.save()
        
        return {
            'id': msg.id,
            'message': msg.message,
            'sender_name': self.user.get_full_name() or self.user.username,
            'is_admin_reply': self.user != ticket.user,
            'created_at': msg.created_at.isoformat(),
            'is_read': False,
        }

    @database_sync_to_async
    def async_trigger_push_notifications(self, text):
        """Re-implementing the push notification logic securely inside an async wrapper."""
        from support.models import SupportTicket
        ticket = SupportTicket.objects.get(ticket_id=self.ticket_id)
        
        if self.user != ticket.user:
            # Admin replied -> Push to user
            if getattr(ticket.user, 'fcm_token', None):
                from truff_admin_panel.firebase_push import send_to_token
                try:
                    send_to_token(
                        ticket.user.fcm_token,
                        "New reply on Support Ticket",
                        f"Support: {text[:50]}{'...' if len(text)>50 else ''}",
                        data={'route': 'support', 'ticket_id': ticket.ticket_id}
                    )
                except Exception as e:
                    logger.error(f"Failed to push reply to user: {e}")
        else:
            # User replied -> Push to admin
            try:
                from truff_admin_panel.push_models import AdminNotification
                AdminNotification.objects.create(
                    title=f"New Message: {ticket.ticket_id}",
                    body=f"{self.user.username}: {text[:50]}{'...' if len(text)>50 else ''}",
                    notification_type='support_alert',
                    target_type='all',
                ).send()
            except Exception as e:
                logger.error(f"Failed to send admin push for ticket {ticket.ticket_id}: {e}")
