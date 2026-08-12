"""ASGI primitives that enforce header-only bearer presentation."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from http.cookies import CookieError, SimpleCookie
from typing import Any
from urllib.parse import parse_qsl

NON_HEADER_BEARER_KEYS = frozenset({"access_token", "bearer_token"})


def non_header_bearer_sources(
    *,
    query_string: bytes | str = b"",
    cookie_header: bytes | str = b"",
    bearer_keys: Iterable[str] = NON_HEADER_BEARER_KEYS,
) -> frozenset[str]:
    """Return the forbidden credential sources present in one HTTP request."""

    if isinstance(query_string, bytes):
        query_string = query_string.decode("utf-8", "ignore")
    if isinstance(cookie_header, bytes):
        cookie_header = cookie_header.decode("latin-1", "ignore")
    keys = frozenset(bearer_keys)
    query_keys = {key for key, _ in parse_qsl(query_string, keep_blank_values=True)}
    cookie = SimpleCookie()
    try:
        cookie.load(cookie_header)
    except CookieError:
        cookie_keys: set[str] = set()
    else:
        cookie_keys = {key.lower() for key in cookie}
    sources = set()
    if query_keys & keys:
        sources.add("query")
    if cookie_keys & keys:
        sources.add("cookie")
    return frozenset(sources)


async def _send_json(send, status: int, payload: dict[str, object], headers=()) -> None:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                *headers,
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class HeaderOnlyBearerMiddleware:
    """Reject access credentials sent through query parameters or cookies."""

    def __init__(
        self,
        app: Callable[..., Any],
        *,
        path: str,
        invalid_token_challenge: str | Callable[[], str],
        bearer_keys: Iterable[str] = NON_HEADER_BEARER_KEYS,
    ):
        self.app = app
        self.path = path
        self.invalid_token_challenge = invalid_token_challenge
        self.bearer_keys = tuple(bearer_keys)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != self.path:
            await self.app(scope, receive, send)
            return
        cookie_header = next(
            (value for key, value in scope.get("headers", []) if key.lower() == b"cookie"),
            b"",
        )
        sources = non_header_bearer_sources(
            query_string=scope.get("query_string", b""),
            cookie_header=cookie_header,
            bearer_keys=self.bearer_keys,
        )
        if sources:
            challenge = self.invalid_token_challenge
            if callable(challenge):
                challenge = challenge()
            await _send_json(
                send,
                401,
                {
                    "error": "invalid_token",
                    "error_description": "Use the Authorization header.",
                },
                headers=(
                    (b"www-authenticate", challenge.encode("ascii")),
                    (b"cache-control", b"no-store"),
                ),
            )
            return
        await self.app(scope, receive, send)
