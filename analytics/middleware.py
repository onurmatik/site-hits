from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.utils.cache import patch_vary_headers


class EventCorsMiddleware:
    event_path = "/api/events"
    collection_paths = {
        event_path,
        "/api/bot-events",
        "/api/server-events",
        "/api/server-events/forget-actor",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path in self.collection_paths:
            origin = request.headers.get("Origin", "")
            if request.path == self.event_path and request.method == "OPTIONS":
                response = HttpResponse(status=204)
                if origin:
                    response["Access-Control-Allow-Origin"] = origin
                    response["Access-Control-Allow-Methods"] = "POST, OPTIONS"
                    response["Access-Control-Allow-Headers"] = "Content-Type"
                    response["Access-Control-Max-Age"] = "86400"
                    patch_vary_headers(response, ["Origin"])
                return response

            content_length = request.META.get("CONTENT_LENGTH")
            if content_length and int(content_length) > settings.SITEHITS_MAX_EVENT_BYTES:
                response = JsonResponse(
                    {"error": {"message": "Event payload is too large."}}, status=413
                )
            else:
                response = self.get_response(request)
            if request.path == self.event_path and origin and response.status_code == 202:
                response["Access-Control-Allow-Origin"] = origin
                patch_vary_headers(response, ["Origin"])
            return response
        return self.get_response(request)
