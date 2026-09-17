"""Read-only progress check for the supervised reminder worker."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3

import config


def is_healthy(database, *, now=None, max_age=180):
    """Require progress from the service itself, not manual browser actions."""
    now = now or datetime.now(timezone.utc)
    if max_age <= 0:
        return False
    try:
        uri = Path(database).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
            row = db.execute("SELECT heartbeat FROM ws_worker WHERE id='service'").fetchone()
        if row is None:
            return False
        return -30 <= (now - datetime.fromisoformat(row[0])).total_seconds() <= max_age
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return False


def main():
    parser = argparse.ArgumentParser(description="Check recent reminder worker progress")
    parser.add_argument("--database", type=Path, default=Path(os.getenv("WORKSPACE_DATABASE_PATH", str(config.ROOT / ".data" / "workspace.sqlite3"))))
    parser.add_argument("--max-age", type=int, default=180)
    args = parser.parse_args()
    if args.max_age <= 0:
        parser.error("--max-age must be greater than zero")
    healthy = is_healthy(args.database, max_age=args.max_age)
    print("Worker is healthy." if healthy else "Worker progress is missing, stale or unreadable.")
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
