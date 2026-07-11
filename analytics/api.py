from django.http import JsonResponse
from ninja import NinjaAPI, Status
from ninja.errors import HttpError
from ninja.security import django_auth

from .ingestion import IngestionError, ingest_event
from .reporting import breakdown, overview, timeseries
from .schemas import AcceptedResponse, ErrorResponse, EventPayload


api = NinjaAPI(title="SiteHits API", version="1.0.0")


def require_superuser(request):
    if not request.user.is_superuser:
        raise HttpError(403, "Superuser access is required.")


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


@api.get("/analytics/overview", auth=django_auth, summary="Get aggregate metrics")
def analytics_overview(request, site: str = "all", period: str = "last7d"):
    require_superuser(request)
    try:
        return overview(site, period)
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc


@api.get("/analytics/timeseries", auth=django_auth, summary="Get time-series metrics")
def analytics_timeseries(
    request,
    site: str = "all",
    period: str = "last7d",
    granularity: str = "auto",
):
    require_superuser(request)
    try:
        return timeseries(site, period, granularity)
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
    require_superuser(request)
    try:
        return breakdown(site, period, dimension, limit)
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc
