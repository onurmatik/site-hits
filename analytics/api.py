from django.http import JsonResponse
from ninja import NinjaAPI, Status
from ninja.errors import HttpError
from ninja.security import django_auth

from websites.models import TrackedSite

from .bot_ingestion import (
    BotAuthenticationError,
    BotIngestionError,
    ingest_bot_event,
)
from .ingestion import IngestionError, ingest_event
from .product_ingestion import (
    ProductAuthenticationError,
    ProductIngestionError,
    forget_actor,
    ingest_server_event,
)
from .product_reporting import product_metrics
from .reporting import bot_traffic, breakdown, overview, site_overviews, timeseries
from .schemas import (
    AcceptedResponse,
    BotAcceptedResponse,
    BotEventPayload,
    ErrorResponse,
    EventPayload,
    ForgetActorPayload,
    ForgetActorResponse,
    ServerAcceptedResponse,
    ServerEventPayload,
)


api = NinjaAPI(title="SiteHits API", version="1.0.0")


def visible_sites(request):
    sites = TrackedSite.objects.filter(is_active=True)
    return sites if request.user.is_superuser else sites.filter(owner=request.user)


@api.post(
    "/events",
    auth=None,
    response={202: AcceptedResponse, 400: ErrorResponse},
    summary="Collect a browser analytics event",
)
def collect_event(request, payload: EventPayload):
    try:
        ingest_event(request, payload)
    except (IngestionError, ValueError) as exc:
        return Status(400, {"error": {"message": str(exc)}})
    response = JsonResponse({"accepted": True}, status=202)
    return response


@api.post(
    "/server-events",
    auth=None,
    response={202: ServerAcceptedResponse, 400: ErrorResponse, 401: ErrorResponse},
    summary="Collect an authenticated product event",
)
def collect_server_event(request, payload: ServerEventPayload):
    try:
        _, duplicate = ingest_server_event(request, payload)
    except ProductAuthenticationError as exc:
        return Status(401, {"error": {"message": str(exc)}})
    except (ProductIngestionError, ValueError) as exc:
        return Status(400, {"error": {"message": str(exc)}})
    return Status(202, {"accepted": True, "duplicate": duplicate})


@api.post(
    "/server-events/forget-actor",
    auth=None,
    response={200: ForgetActorResponse, 401: ErrorResponse},
    summary="Delete product events for one actor",
)
def forget_server_actor(request, payload: ForgetActorPayload):
    try:
        deleted = forget_actor(request, payload.actor_id)
    except ProductAuthenticationError as exc:
        return Status(401, {"error": {"message": str(exc)}})
    return {"deleted_events": deleted}


@api.post(
    "/bot-events",
    auth=None,
    response={202: BotAcceptedResponse, 400: ErrorResponse, 401: ErrorResponse},
    summary="Collect a server-side bot request",
)
def collect_bot_event(request, payload: BotEventPayload):
    try:
        event = ingest_bot_event(request, payload)
    except BotAuthenticationError as exc:
        return Status(401, {"error": {"message": str(exc)}})
    except BotIngestionError as exc:
        return Status(400, {"error": {"message": str(exc)}})
    return Status(
        202,
        {
            "accepted": event is not None,
            "classification": "known_crawler" if event is not None else "unrecognized",
        },
    )


@api.get("/analytics/overview", auth=django_auth, summary="Get aggregate metrics")
def analytics_overview(request, site: str = "all", period: str = "last7d"):
    try:
        return overview(site, period, visible_sites(request))
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc


@api.get(
    "/analytics/sites-overview",
    auth=django_auth,
    summary="Get metrics grouped by site",
)
def analytics_sites_overview(request, site: str = "all", period: str = "last7d"):
    if site != "all":
        raise HttpError(400, "Site comparison is only available for all sites.")
    try:
        return site_overviews(period, visible_sites(request))
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc


@api.get("/analytics/timeseries", auth=django_auth, summary="Get time-series metrics")
def analytics_timeseries(
    request,
    site: str = "all",
    period: str = "last7d",
    granularity: str = "auto",
):
    try:
        return timeseries(site, period, granularity, visible_sites(request))
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc


@api.get(
    "/analytics/breakdowns/{dimension}",
    auth=django_auth,
    summary="Get a ranked analytics breakdown",
)
def analytics_breakdown(
    request,
    dimension: str,
    site: str = "all",
    period: str = "last7d",
    limit: int = 8,
):
    try:
        return breakdown(site, period, dimension, limit, visible_sites(request))
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc


@api.get(
    "/analytics/bots",
    auth=django_auth,
    summary="Get server-side bot traffic analytics",
)
def analytics_bots(request, site: str = "all", period: str = "last7d", limit: int = 8):
    try:
        return bot_traffic(site, period, limit, visible_sites(request))
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc


@api.get(
    "/analytics/product-metrics",
    auth=django_auth,
    summary="Get configured activation and product metrics",
)
def analytics_product_metrics(request, site: str, period: str = "last7d"):
    try:
        return product_metrics(site, period, visible_sites(request))
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc
