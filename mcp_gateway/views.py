from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .oauth import (
    ConsentRequestError,
    get_authorization_request,
    resolve_authorization_request,
)
from .versioning import integration_manifest


def _harden_consent_response(response):
    response["Referrer-Policy"] = "no-referrer"
    response["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'"
    )
    return response


def agent_manifest(request):
    response = JsonResponse(integration_manifest())
    response["Cache-Control"] = "public, max-age=300"
    return response


def mcp_documentation(request):
    return render(
        request,
        "mcp_gateway/documentation.html",
        {"mcp_resource_url": settings.SITEHITS_MCP_RESOURCE_URL},
    )


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def oauth_consent(request):
    request_id = request.POST.get("request") or request.GET.get("request")
    if not request_id:
        return HttpResponseBadRequest("Missing authorization request.")
    authorization_request = get_authorization_request(request_id)
    if authorization_request is None:
        return _harden_consent_response(
            render(
                request,
                "mcp_gateway/oauth_consent.html",
                {"authorization_request": None},
                status=400,
            )
        )

    if request.method == "POST":
        action = request.POST.get("action")
        if action not in {"approve", "deny"}:
            return HttpResponseBadRequest("Choose approve or deny.")
        try:
            redirect_uri = resolve_authorization_request(
                request_id,
                request.user,
                approved=action == "approve",
            )
        except ConsentRequestError:
            return HttpResponseBadRequest("Authorization request is no longer available.")
        return _harden_consent_response(HttpResponseRedirect(redirect_uri))

    client_metadata = authorization_request.client.metadata
    return _harden_consent_response(
        render(
            request,
            "mcp_gateway/oauth_consent.html",
            {
                "authorization_request": authorization_request,
                "client_name": client_metadata.get("client_name") or "MCP client",
            },
        )
    )
