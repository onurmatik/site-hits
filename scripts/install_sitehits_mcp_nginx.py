#!/usr/bin/env python3
"""Idempotently attach SiteHits MCP routes to the canonical HTTPS server."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path


BEGIN = "    # BEGIN SITEHITS MCP DEPLOY CONTRACT"
END = "    # END SITEHITS MCP DEPLOY CONTRACT"


def _https_server_bounds(text: str) -> tuple[int, int]:
    listen = text.find("listen 443 ssl")
    if listen < 0:
        raise ValueError("Nginx site has no HTTPS server block")
    start = text.rfind("server {", 0, listen)
    if start < 0:
        raise ValueError("cannot locate the HTTPS server block start")
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return start, index + 1
    raise ValueError("cannot locate the HTTPS server block end")


def render_site(text: str, include_path: str) -> str:
    if BEGIN in text or END in text:
        if text.count(BEGIN) != 1 or text.count(END) != 1:
            raise ValueError("Nginx deploy-contract markers are inconsistent")
        return text
    _start, end = _https_server_bounds(text)
    insert_at = end - 1
    addition = f"\n{BEGIN}\n    include {include_path};\n{END}\n"
    return text[:insert_at] + addition + text[insert_at:]


def atomic_write(path: Path, content: str) -> None:
    mode = path.stat().st_mode & 0o777
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-config", type=Path, required=True)
    parser.add_argument("--include-path", required=True)
    args = parser.parse_args(argv)
    original = args.site_config.read_text(encoding="utf-8")
    rendered = render_site(original, args.include_path)
    if rendered != original:
        atomic_write(args.site_config, rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
