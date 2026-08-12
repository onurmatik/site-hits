"""Fail-closed Client ID Metadata Document validation and retrieval."""

from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import json
import socket
import ssl
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from string import hexdigits
from unicodedata import category
from urllib.parse import unquote, urlsplit

import urllib3

from .oauth import normalize_scopes
from .redirects import LOOPBACK_HOSTS, validate_registered_redirect_uri

NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")


class CIMDError(ValueError):
    """A safe, categorized CIMD validation or retrieval failure."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class CIMDDocument:
    """Validated public-client fields derived from one metadata document."""

    client_id: str
    client_name: str
    redirect_uris: tuple[str, ...]
    application_type: str
    scopes: tuple[str, ...]
    max_age_seconds: int
    document_sha256: str


def is_cimd_client_id(client_id: object) -> bool:
    """Return whether a value can enter the strict CIMD URL validator."""

    return isinstance(client_id, str) and client_id.startswith("https://")


def _reject_unsafe_url_text(value: str) -> None:
    if not isinstance(value, str):
        raise CIMDError("invalid_client_id", "client_id must be a string")
    if any(ord(character) <= 0x20 or ord(character) == 0x7F for character in value):
        raise CIMDError("invalid_client_id", "client_id contains unsafe whitespace")
    if "\\" in value or "*" in value:
        raise CIMDError("invalid_client_id", "client_id contains an unsafe character")
    if "?" in value or "#" in value:
        raise CIMDError("invalid_client_id", "client_id must not contain query or fragment")
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise CIMDError(
            "invalid_client_id",
            "client_id must be an ASCII URI",
        ) from exc
    for index, character in enumerate(value):
        if character == "%" and (
            index + 2 >= len(value)
            or value[index + 1] not in hexdigits
            or value[index + 2] not in hexdigits
        ):
            raise CIMDError("invalid_client_id", "client_id has an invalid percent escape")


def validate_cimd_client_id(client_id: str):
    """Validate the exact HTTPS metadata URL without normalizing it."""

    _reject_unsafe_url_text(client_id)
    try:
        parsed = urlsplit(client_id)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise CIMDError("invalid_client_id", "client_id is not a valid URL") from exc
    if parsed.scheme != "https" or not client_id.startswith("https://"):
        raise CIMDError("invalid_client_id", "client_id must use lowercase https")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise CIMDError("invalid_client_id", "client_id authority is invalid")
    if parsed.netloc.endswith(":"):
        raise CIMDError("invalid_client_id", "client_id authority has an empty port")
    try:
        parsed.hostname.encode("ascii")
    except UnicodeEncodeError as exc:
        raise CIMDError("invalid_client_id", "client_id host must use ASCII IDNA form") from exc
    if parsed.netloc != parsed.netloc.lower():
        raise CIMDError("invalid_client_id", "client_id authority must be lowercase")
    if not parsed.path or not parsed.path.startswith("/"):
        raise CIMDError("invalid_client_id", "client_id must contain an absolute path")
    if any(
        segment.lower() in {".", ".."}
        for segment in unquote(parsed.path).split("/")
    ):
        raise CIMDError("invalid_client_id", "client_id must not contain dot path segments")
    if len(client_id) > 255:
        raise CIMDError("invalid_client_id", "client_id exceeds 255 characters")
    if port is not None and not 1 <= port <= 65535:
        raise CIMDError("invalid_client_id", "client_id port is invalid")
    return parsed


def _embedded_ipv4(address: ipaddress.IPv6Address):
    if address.ipv4_mapped is not None:
        return (address.ipv4_mapped,)
    if address.sixtofour is not None:
        return (address.sixtofour,)
    if address.teredo is not None:
        return address.teredo
    if address in NAT64_PREFIX:
        return (ipaddress.IPv4Address(int(address) & 0xFFFFFFFF),)
    return ()


def address_is_public(value: str) -> bool:
    """Reject every non-global address, including IPv4 embedded in IPv6."""

    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        embedded = _embedded_ipv4(address)
        if embedded:
            return all(candidate.is_global for candidate in embedded)
    return address.is_global


def resolve_public_addresses(
    hostname: str,
    port: int,
    *,
    resolver: Callable[..., list] = socket.getaddrinfo,
) -> tuple[str, ...]:
    """Resolve once and fail if any returned address is not globally routable."""

    try:
        answers = resolver(hostname, port, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise CIMDError("dns_failure", "client_id host could not be resolved") from exc
    addresses: list[str] = []
    for answer in answers:
        address = answer[4][0]
        if not address_is_public(address):
            raise CIMDError("non_public_address", "client_id host is not public")
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise CIMDError("dns_failure", "client_id host returned no addresses")
    return tuple(addresses)


def _cache_max_age(
    cache_control: str,
    *,
    minimum: int,
    maximum: int,
    response_age_seconds: int = 0,
) -> int:
    if minimum <= 0 or maximum < minimum:
        raise ValueError("CIMD cache bounds are invalid")
    if response_age_seconds < 0:
        raise ValueError("CIMD response age must not be negative")
    directives: dict[str, list[str | None]] = {}
    for raw_directive in cache_control.split(","):
        name, separator, value = raw_directive.strip().partition("=")
        if not name:
            continue
        directives.setdefault(name.lower(), []).append(
            value.strip().strip('"') if separator else None
        )
    if "no-store" in directives or "no-cache" in directives:
        selected = minimum
    elif "max-age" in directives:
        try:
            values = [int(value) for value in directives["max-age"]]
        except (TypeError, ValueError) as exc:
            raise CIMDError("invalid_cache", "Cache-Control max-age is invalid") from exc
        if any(value < 0 for value in values):
            raise CIMDError("invalid_cache", "Cache-Control max-age is invalid")
        selected = min(values)
    else:
        selected = maximum
    remaining = max(0, selected - response_age_seconds)
    return max(minimum, min(remaining, maximum))


def _load_json_object(body: bytes) -> dict[str, object]:
    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise CIMDError("invalid_document", "metadata contains duplicate keys")
            result[key] = value
        return result

    def reject_constant(_value):
        raise CIMDError("invalid_document", "metadata contains a non-JSON number")

    try:
        payload = json.loads(
            body,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except CIMDError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, TypeError) as exc:
        raise CIMDError("invalid_document", "metadata is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise CIMDError("invalid_document", "metadata must be a JSON object")
    return payload


def _persistable_text(value: object, *, required: bool = False) -> bool:
    if not isinstance(value, str) or (required and not value):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return not any(
        ord(character) < 0x20
        or 0x7F <= ord(character) <= 0x9F
        or category(character) in {"Zl", "Zp"}
        for character in value
    )


def _application_type(redirect_uris: tuple[str, ...], declared: object) -> str:
    native = all(
        urlsplit(uri).scheme == "http"
        and urlsplit(uri).hostname in LOOPBACK_HOSTS | {"localhost"}
        for uri in redirect_uris
    )
    web = all(urlsplit(uri).scheme == "https" for uri in redirect_uris)
    inferred = "native" if native else "web" if web else ""
    if declared is None:
        declared = inferred
    if declared not in {"native", "web"} or declared != inferred:
        raise CIMDError(
            "invalid_document",
            "application_type does not match the redirect URI profile",
        )
    return declared


def validate_cimd_document(
    body: bytes,
    *,
    expected_client_id: str,
    cache_control: str,
    supported_scopes: Iterable[str],
    required_scopes: Iterable[str],
    default_scopes: Iterable[str],
    minimum_cache_seconds: int,
    maximum_cache_seconds: int,
    max_redirect_uris: int = 10,
    response_age_seconds: int = 0,
) -> CIMDDocument:
    """Validate one bounded JSON document into the fixed public-client profile."""

    validate_cimd_client_id(expected_client_id)
    payload = _load_json_object(body)
    required_fields = {"client_id", "client_name", "redirect_uris"}
    if not required_fields.issubset(payload):
        raise CIMDError("invalid_document", "metadata is missing a required field")
    if payload["client_id"] != expected_client_id:
        raise CIMDError("client_id_mismatch", "metadata client_id does not match its URL")
    client_name = payload["client_name"]
    if not _persistable_text(client_name, required=True) or len(client_name) > 255:
        raise CIMDError("invalid_document", "client_name is invalid")
    if "client_secret" in payload or "client_secret_expires_at" in payload:
        raise CIMDError("invalid_document", "public metadata must not contain a secret")
    if payload.get("token_endpoint_auth_method", "none") != "none":
        raise CIMDError("invalid_document", "token_endpoint_auth_method must be none")
    response_types = payload.get("response_types", ["code"])
    if response_types != ["code"]:
        raise CIMDError("invalid_document", "response_types must contain only code")
    grant_types = payload.get(
        "grant_types",
        ["authorization_code", "refresh_token"],
    )
    if (
        not isinstance(grant_types, list)
        or len(grant_types) != 2
        or set(grant_types) != {"authorization_code", "refresh_token"}
    ):
        raise CIMDError("invalid_document", "grant_types are unsupported")
    redirect_values = payload["redirect_uris"]
    if (
        not isinstance(redirect_values, list)
        or not 1 <= len(redirect_values) <= max_redirect_uris
        or not all(isinstance(uri, str) for uri in redirect_values)
        or len(redirect_values) != len(set(redirect_values))
    ):
        raise CIMDError("invalid_document", "redirect_uris are invalid")
    try:
        for uri in redirect_values:
            validate_registered_redirect_uri(uri, allow_localhost=True)
    except ValueError as exc:
        raise CIMDError("invalid_document", "redirect_uris are invalid") from exc
    redirect_uris = tuple(redirect_values)
    application_type = _application_type(
        redirect_uris,
        payload.get("application_type"),
    )
    raw_scope = payload.get("scope", " ".join(default_scopes))
    if not isinstance(raw_scope, str):
        raise CIMDError("invalid_document", "scope must be a string")
    try:
        scopes = tuple(
            normalize_scopes(
                raw_scope.split(),
                supported_scopes=supported_scopes,
                required_scopes=required_scopes,
            )
        )
    except ValueError as exc:
        raise CIMDError("invalid_document", "scope is invalid") from exc
    return CIMDDocument(
        client_id=expected_client_id,
        client_name=client_name,
        redirect_uris=redirect_uris,
        application_type=application_type,
        scopes=scopes,
        max_age_seconds=_cache_max_age(
            cache_control,
            minimum=minimum_cache_seconds,
            maximum=maximum_cache_seconds,
            response_age_seconds=response_age_seconds,
        ),
        document_sha256=hashlib.sha256(body).hexdigest(),
    )


class SafeCIMDFetcher:
    """Fetch CIMD over one DNS-pinned, redirect-free, size-bounded HTTPS request."""

    _semaphore_lock = threading.Lock()
    _semaphores: dict[int, threading.BoundedSemaphore] = {}

    def __init__(
        self,
        *,
        timeout_seconds: float,
        max_document_bytes: int,
        minimum_cache_seconds: int,
        maximum_cache_seconds: int,
        max_concurrent_fetches: int,
        resolver: Callable[..., list] = socket.getaddrinfo,
    ):
        if timeout_seconds <= 0 or max_document_bytes <= 0 or max_concurrent_fetches <= 0:
            raise ValueError("CIMD fetch limits must be positive")
        self.timeout_seconds = timeout_seconds
        self.max_document_bytes = max_document_bytes
        self.minimum_cache_seconds = minimum_cache_seconds
        self.maximum_cache_seconds = maximum_cache_seconds
        self.max_concurrent_fetches = max_concurrent_fetches
        self.resolver = resolver

    def _semaphore(self):
        with self._semaphore_lock:
            return self._semaphores.setdefault(
                self.max_concurrent_fetches,
                threading.BoundedSemaphore(self.max_concurrent_fetches),
            )

    @contextlib.contextmanager
    def _fetch_slot(self):
        semaphore = self._semaphore()
        acquired = semaphore.acquire(blocking=False)
        try:
            if not acquired:
                raise CIMDError("capacity_exceeded", "CIMD fetch capacity is exhausted")
            yield
        finally:
            if acquired:
                semaphore.release()

    def fetch(
        self,
        client_id: str,
        *,
        supported_scopes: Iterable[str],
        required_scopes: Iterable[str],
        default_scopes: Iterable[str],
    ) -> CIMDDocument:
        parsed = validate_cimd_client_id(client_id)
        port = parsed.port or 443
        addresses = resolve_public_addresses(
            parsed.hostname,
            port,
            resolver=self.resolver,
        )
        deadline = time.monotonic() + self.timeout_seconds
        last_error: Exception | None = None
        with self._fetch_slot():
            for address in addresses:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                pool = urllib3.HTTPSConnectionPool(
                    host=address,
                    port=port,
                    timeout=urllib3.Timeout(
                        connect=remaining,
                        read=remaining,
                        total=remaining,
                    ),
                    retries=False,
                    maxsize=1,
                    ssl_context=ssl.create_default_context(),
                    server_hostname=parsed.hostname,
                    assert_hostname=parsed.hostname,
                )
                try:
                    response = pool.urlopen(
                        "GET",
                        parsed.path,
                        headers={
                            "Host": parsed.netloc,
                            "Accept": "application/json, application/*+json",
                            "Accept-Encoding": "identity",
                        },
                        redirect=False,
                        preload_content=False,
                        decode_content=False,
                    )
                    try:
                        return self._read_response(
                            response,
                            client_id=client_id,
                            supported_scopes=supported_scopes,
                            required_scopes=required_scopes,
                            default_scopes=default_scopes,
                        )
                    finally:
                        response.release_conn()
                except CIMDError:
                    raise
                except (OSError, urllib3.exceptions.HTTPError) as exc:
                    last_error = exc
                finally:
                    pool.close()
        raise CIMDError("fetch_failed", "client metadata could not be fetched") from last_error

    def _read_response(
        self,
        response,
        *,
        client_id: str,
        supported_scopes: Iterable[str],
        required_scopes: Iterable[str],
        default_scopes: Iterable[str],
    ) -> CIMDDocument:
        if response.status != 200:
            raise CIMDError("http_status", "client metadata returned a non-200 status")
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
        if content_type != "application/json" and not (
            content_type.startswith("application/") and content_type.endswith("+json")
        ):
            raise CIMDError("content_type", "client metadata is not JSON")
        if response.headers.get("Content-Encoding", "identity").lower() not in {
            "",
            "identity",
        }:
            raise CIMDError("content_encoding", "client metadata must not be compressed")
        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_document_bytes:
                    raise CIMDError("document_too_large", "client metadata is too large")
            except ValueError as exc:
                raise CIMDError("invalid_document", "Content-Length is invalid") from exc
        body = response.read(self.max_document_bytes + 1, decode_content=False)
        if len(body) > self.max_document_bytes:
            raise CIMDError("document_too_large", "client metadata is too large")
        raw_age = response.headers.get("Age", "0")
        try:
            response_age_seconds = int(raw_age)
        except (TypeError, ValueError) as exc:
            raise CIMDError("invalid_cache", "Age header is invalid") from exc
        if response_age_seconds < 0:
            raise CIMDError("invalid_cache", "Age header is invalid")
        return validate_cimd_document(
            body,
            expected_client_id=client_id,
            cache_control=response.headers.get("Cache-Control", ""),
            supported_scopes=supported_scopes,
            required_scopes=required_scopes,
            default_scopes=default_scopes,
            minimum_cache_seconds=self.minimum_cache_seconds,
            maximum_cache_seconds=self.maximum_cache_seconds,
            response_age_seconds=response_age_seconds,
        )
