from functools import lru_cache
import hashlib
import hmac
import ipaddress
import re
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from geoip2.database import Reader
from user_agents import parse as parse_user_agent


EVENT_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_:-]{0,63}$")
PROPERTY_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
UTM_FIELDS = ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content")


def normalize_hostname(value):
    if not value:
        return ""
    hostname = value.strip().lower().rstrip(".")
    if hostname.startswith("[") and "]" in hostname:
        return hostname[1 : hostname.index("]")]
    return hostname.split(":", 1)[0]


def hostname_allowed(hostname, patterns):
    hostname = normalize_hostname(hostname)
    for raw_pattern in patterns:
        pattern = normalize_hostname(raw_pattern)
        if pattern.startswith("*."):
            suffix = pattern[1:]
            if hostname.endswith(suffix) and hostname != suffix[1:]:
                return True
        elif hostname == pattern:
            return True
    return False


def origin_hostname(origin):
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"}:
        return ""
    return normalize_hostname(parsed.hostname or "")


def sanitized_page(url):
    parsed = urlsplit(url)
    path = parsed.path or "/"
    query = parse_qs(parsed.query, keep_blank_values=False)
    utm = {field: (query.get(field, [""])[0][:255]) for field in UTM_FIELDS}
    return normalize_hostname(parsed.hostname or ""), path[:2048], utm


def sanitized_referrer(referrer):
    if not referrer:
        return "", ""
    parsed = urlsplit(referrer)
    return normalize_hostname(parsed.hostname or "")[:255], (parsed.path or "/")[:2048]


def client_ip(request):
    if settings.SITEHITS_TRUST_PROXY_HEADERS:
        connecting_ip = request.META.get("HTTP_CF_CONNECTING_IP", "").strip()
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",", 1)[0].strip()
        candidate = connecting_ip or forwarded or request.META.get("REMOTE_ADDR", "")
    else:
        candidate = request.META.get("REMOTE_ADDR", "")
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return "0.0.0.0"


def daily_visitor_hash(site, ip_address, user_agent, day):
    daily_key = hmac.new(
        settings.SITEHITS_HASH_SECRET.encode(),
        day.isoformat().encode(),
        hashlib.sha256,
    ).digest()
    value = f"{site.public_key}|{ip_address}|{user_agent}".encode()
    return hmac.new(daily_key, value, hashlib.sha256).hexdigest()


def device_details(user_agent):
    parsed = parse_user_agent(user_agent or "")
    if parsed.is_bot:
        device = "bot"
    elif parsed.is_mobile:
        device = "mobile"
    elif parsed.is_tablet:
        device = "tablet"
    else:
        device = "desktop"
    return {
        "is_bot": parsed.is_bot,
        "device": device,
        "browser": parsed.browser.family[:100] or "Unknown",
        "operating_system": parsed.os.family[:100] or "Unknown",
    }


@lru_cache(maxsize=2)
def geoip_reader(path):
    return Reader(path) if path else None


def country_for_ip(ip_address):
    path = settings.SITEHITS_GEOIP_DB_PATH
    if not path:
        return "", ""
    try:
        response = geoip_reader(path).country(ip_address)
        return (response.country.iso_code or "")[:2], (response.country.name or "")[:100]
    except Exception:
        return "", ""


def sanitized_properties(properties):
    if len(properties) > 10:
        raise ValueError("A maximum of 10 event properties is allowed.")
    cleaned = {}
    for key, value in properties.items():
        normalized_key = str(key).lower()
        if not PROPERTY_NAME_PATTERN.fullmatch(normalized_key):
            raise ValueError(f"Invalid property name: {key}")
        rendered = "" if value is None else str(value)
        cleaned[normalized_key] = rendered[:255]
    return cleaned
