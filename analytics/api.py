from django.http import JsonResponse
from ninja import NinjaAPI, Status
from ninja.errors import HttpError
from ninja.security import django_auth

from websites.models import TrackedSite

from .ingestion import IngestionError, ingest_event
from .reporting import breakdown, overview, timeseries
from .schemas import AcceptedResponse, ErrorResponse, EventPayload


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


@api.get("/analytics/overview", auth=django_auth, summary="Get aggregate metrics")
def analytics_overview(request, site: str = "all", period: str = "last7d"):
    try:
        return overview(site, period, visible_sites(request))
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
