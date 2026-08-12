"""Strict native-app and HTTPS OAuth redirect URI policy."""

from ipaddress import ip_address
from string import ascii_letters, digits, hexdigits
from urllib.parse import urlsplit

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})
RFC3986_RAW_URI_CHARACTERS = frozenset(
    ascii_letters + digits + "-._~:/?[]@!$&'()+,;=%"
)


def _reject_unsafe_raw_uri(uri: str) -> None:
    """Reject characters that URL parsers may strip or reinterpret."""

    if not isinstance(uri, str):
        raise TypeError("Redirect URI must be a string.")
    if any(ord(character) <= 0x20 or ord(character) == 0x7F for character in uri):
        raise ValueError("Redirect URI must not contain ASCII whitespace or control characters.")
    if "#" in uri:
        raise ValueError("Redirect URI must not contain a fragment delimiter.")
    if "*" in uri:
        raise ValueError("Redirect URI must not contain a wildcard.")
    if "\\" in uri:
        raise ValueError("Redirect URI must not contain a backslash.")
    if any(character not in RFC3986_RAW_URI_CHARACTERS for character in uri):
        raise ValueError("Redirect URI must contain only RFC 3986 ASCII characters.")
    if uri.endswith("?"):
        raise ValueError("Redirect URI must not end with an empty query delimiter.")
    for index, character in enumerate(uri):
        if character == "%" and (
            index + 2 >= len(uri)
            or uri[index + 1] not in hexdigits
            or uri[index + 2] not in hexdigits
        ):
            raise ValueError("Redirect URI contains an invalid percent escape.")


def _parsed(uri: str):
    _reject_unsafe_raw_uri(uri)
    try:
        parsed = urlsplit(uri)
        _ = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError("Redirect URI is malformed.") from exc
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("Redirect URI contains a forbidden component.")
    return parsed


def validate_registered_redirect_uri(uri: str, *, allow_localhost: bool = False) -> str:
    """Accept exact HTTPS callbacks and selected RFC 8252 loopback callbacks."""

    parsed = _parsed(uri)
    hostname = parsed.hostname
    try:
        literal_address = ip_address(hostname)
    except ValueError:
        literal_address = None
    if literal_address is not None and (
        literal_address.is_unspecified
        or (parsed.scheme == "https" and not literal_address.is_global)
    ):
        raise ValueError("Redirect URI contains an unsupported host.")
    if hostname == "localhost" and (
        not allow_localhost
        or parsed.scheme != "http"
        or not uri.startswith("http://localhost")
    ):
        raise ValueError("Redirect URI contains an unsupported host.")
    if (
        parsed.scheme == "https"
        and uri.startswith("https://")
    ):
        return uri
    loopback_hosts = LOOPBACK_HOSTS | ({"localhost"} if allow_localhost else set())
    if (
        parsed.scheme == "http"
        and uri.startswith("http://")
        and hostname in loopback_hosts
    ):
        return uri
    raise ValueError(
        "Redirect URI must use HTTPS or an explicitly supported HTTP loopback host."
    )


def redirect_uri_matches(
    registered_uri: str,
    requested_uri: str,
    *,
    allow_localhost: bool = False,
) -> bool:
    """Match exactly, except that an approved loopback callback may vary its port."""

    try:
        validate_registered_redirect_uri(registered_uri, allow_localhost=allow_localhost)
        validate_registered_redirect_uri(requested_uri, allow_localhost=allow_localhost)
        registered = _parsed(registered_uri)
        requested = _parsed(requested_uri)
    except (TypeError, ValueError):
        return False
    if registered_uri == requested_uri:
        return True
    loopback_hosts = LOOPBACK_HOSTS | ({"localhost"} if allow_localhost else set())
    return (
        registered.scheme == requested.scheme == "http"
        and registered.hostname == requested.hostname
        and registered.hostname in loopback_hosts
        and registered.path == requested.path
        and registered.query == requested.query
    )
