import time


class RequestDebugMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.time()

        print("\n==============================")
        print("[API HIT]", request.method, request.get_full_path())
        print("Client IP:", request.META.get('REMOTE_ADDR'))
        print("Auth:", request.META.get('HTTP_AUTHORIZATION'))
        print("==============================")

        response = self.get_response(request)

        duration = round((time.time() - start) * 1000, 2)

        print("[STATUS]", response.status_code)
        print("[TIME]", duration, "ms")
        print("==============================\n")

        return response
