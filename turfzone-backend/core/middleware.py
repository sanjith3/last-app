import logging
import time
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)

class RequestLogMiddleware(MiddlewareMixin):
    def process_request(self, request):
        request.start_time = time.time()
        
        # Print to console (guaranteed to show)
        print(f"\n{'='*60}")
        print(f"🔥 REQUEST: {request.method} {request.get_full_path()}")
        print(f"📡 Headers: Content-Type: {request.headers.get('Content-Type', 'N/A')}")
        print(f"👤 User: {request.user if hasattr(request, 'user') and request.user.is_authenticated else 'Anonymous'}")
        
        # Safely read body length without consuming stream for subsequent middleware
        try:
            body_len = len(request.body) if request.body else 0
        except Exception:
            body_len = 'Unknown'
            
        print(f"📦 Body length: {body_len}")
        print(f"{'='*60}\n")
        
        return None
    
    def process_response(self, request, response):
        duration = time.time() - getattr(request, 'start_time', time.time())
        
        # Print to console
        print(f"\n{'='*60}")
        print(f"✅ RESPONSE: {response.status_code} ({duration:.2f}s)")
        print(f"📦 Body length: {len(response.content) if hasattr(response, 'content') else 'N/A'}")
        print(f"{'='*60}\n")
        
        return response
