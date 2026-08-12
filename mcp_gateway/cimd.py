"""SiteHits persistence adapter for fail-closed CIMD resolution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django_embedded_mcp.cimd import (
    CIMDDocument,
    CIMDError,
    SafeCIMDFetcher,
    is_cimd_client_id,
    validate_cimd_client_id,
)
from oauth2_provider.models import AbstractApplication, get_application_model


class CIMDResolutionError(ValueError):
    """A categorized application-resolution failure safe for audit metadata."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CIMDResolution:
    application: object
    source: str
    document_sha256: str


def fetch_cimd_document(client_id: str) -> CIMDDocument:
    """Fetch one document with the product's fixed security limits."""

    fetcher = SafeCIMDFetcher(
        timeout_seconds=settings.SITEHITS_MCP_CIMD_FETCH_TIMEOUT_SECONDS,
        max_document_bytes=settings.SITEHITS_MCP_CIMD_MAX_DOCUMENT_BYTES,
        minimum_cache_seconds=settings.SITEHITS_MCP_CIMD_MIN_CACHE_SECONDS,
        maximum_cache_seconds=settings.SITEHITS_MCP_CIMD_MAX_CACHE_SECONDS,
        max_concurrent_fetches=settings.SITEHITS_MCP_CIMD_MAX_CONCURRENT_FETCHES,
    )
    return fetcher.fetch(
        client_id,
        supported_scopes=settings.SITEHITS_MCP_OAUTH_SCOPES,
        required_scopes=settings.SITEHITS_MCP_BOOTSTRAP_SCOPES,
        default_scopes=settings.SITEHITS_MCP_OAUTH_SCOPES,
    )


def _existing_application(client_id: str):
    Application = get_application_model()
    try:
        return Application.objects.get(client_id=client_id)
    except (Application.DoesNotExist, ValueError):
        return None


def _validate_existing_cimd(application, *, now):
    if application.registration_source != application.RegistrationSource.CIMD:
        raise CIMDResolutionError("client_id_collision")
    if application.revoked_at is not None:
        raise CIMDResolutionError("client_revoked")
    if application.cimd_expires_at is not None and application.cimd_expires_at > now:
        return CIMDResolution(
            application=application,
            source="cache",
            document_sha256=str(application.metadata.get("document_sha256", "")),
        )
    return None


def _apply_document(application, document: CIMDDocument, *, now) -> None:
    application.user = None
    application.client_id = document.client_id
    application.name = document.client_name
    application.redirect_uris = " ".join(document.redirect_uris)
    application.client_type = AbstractApplication.CLIENT_PUBLIC
    application.authorization_grant_type = AbstractApplication.GRANT_AUTHORIZATION_CODE
    application.client_secret = ""
    application.hash_client_secret = False
    application.skip_authorization = False
    application.registration_source = AbstractApplication.RegistrationSource.CIMD
    application.cimd_expires_at = now + timedelta(seconds=document.max_age_seconds)
    application.allowed_scopes = list(document.scopes)
    application.metadata = {
        "registration_method": "cimd",
        "application_type": document.application_type,
        "document_sha256": document.document_sha256,
    }


def resolve_cimd_application(
    client_id: str,
    *,
    now=None,
    fetch_document=None,
) -> CIMDResolution:
    """Resolve a fresh CIMD application; stale fetch failures fail closed."""

    if not settings.SITEHITS_MCP_CIMD_ENABLED or not is_cimd_client_id(client_id):
        raise CIMDResolutionError("not_cimd")
    try:
        validate_cimd_client_id(client_id)
    except CIMDError as exc:
        raise CIMDResolutionError(exc.code) from exc
    now = now or timezone.now()
    fetch_document = fetch_document or fetch_cimd_document
    existing = _existing_application(client_id)
    if existing is not None:
        cached = _validate_existing_cimd(existing, now=now)
        if cached is not None:
            return cached
    try:
        document = fetch_document(client_id)
    except CIMDError as exc:
        raise CIMDResolutionError(exc.code) from exc
    if document.client_id != client_id:
        raise CIMDResolutionError("client_id_mismatch")

    Application = get_application_model()
    created = False
    try:
        with transaction.atomic():
            application = (
                Application.objects.select_for_update()
                .filter(client_id=client_id)
                .first()
            )
            if application is None:
                created = True
                application = Application(client_id=client_id)
            elif application.registration_source != application.RegistrationSource.CIMD:
                raise CIMDResolutionError("client_id_collision")
            elif application.revoked_at is not None:
                raise CIMDResolutionError("client_revoked")
            _apply_document(application, document, now=now)
            try:
                application.full_clean(validate_unique=False)
            except ValidationError as exc:
                raise CIMDResolutionError("invalid_document") from exc
            application.save()
    except IntegrityError as exc:
        winner = _existing_application(client_id)
        if winner is None:
            raise CIMDResolutionError("persistence_failure") from exc
        cached = _validate_existing_cimd(winner, now=now)
        if cached is None:
            raise CIMDResolutionError("concurrent_resolution") from exc
        return cached
    return CIMDResolution(
        application=application,
        source="created" if created else "refreshed",
        document_sha256=document.document_sha256,
    )
