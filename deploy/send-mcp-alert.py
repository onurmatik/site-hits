#!/usr/bin/env python3
"""Send a bounded generic Stage 1 alert without exposing the webhook in argv."""

from __future__ import annotations

import json
import os
import sys
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def main() -> int:
    webhook = os.environ.get("SITEHITS_MCP_ALERT_WEBHOOK_URL", "")
    parsed = urlsplit(webhook)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        print("SITEHITS_MCP_ALERT_WEBHOOK_URL must be an HTTPS URL without userinfo", file=sys.stderr)
        return 78
    if sys.argv[1:] == ["--check"]:
        return 0
    unit = sys.argv[1] if len(sys.argv) == 2 else "unknown"
    payload = json.dumps(
        {
            "source": "sitehits",
            "alert": "mcp_stage1_health_failure",
            "unit": unit[:200],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    request = Request(
        webhook,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "sitehits-stage1-alert/1"},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:  # noqa: S310 - validated HTTPS URL.
        if not 200 <= response.status < 300:
            raise RuntimeError("Alert receiver returned a non-success status.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
