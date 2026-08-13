"""Create a PostgreSQL custom-format backup without putting credentials in argv."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: backup_database.py <output.dump>")
    database_url = os.environ.get("DATABASE_URL", "")
    parsed = urlsplit(database_url)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        raise RuntimeError("DATABASE_URL must identify PostgreSQL for deployment backup.")
    database_name = unquote(parsed.path.lstrip("/"))
    if not database_name:
        raise RuntimeError("DATABASE_URL must include a database name.")
    output = Path(sys.argv[1])
    environment = os.environ.copy()
    if parsed.password is not None:
        environment["PGPASSWORD"] = unquote(parsed.password)
    command = [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--file",
        str(output),
        "--host",
        parsed.hostname,
        "--port",
        str(parsed.port or 5432),
        "--dbname",
        database_name,
    ]
    if parsed.username is not None:
        command.extend(["--username", unquote(parsed.username)])
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(command, check=True, env=environment)
    output.chmod(0o600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
