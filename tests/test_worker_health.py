from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys

import pytest

from worker_health import is_healthy
from workspace_store import WorkspaceStore


NOW = datetime(2026, 9, 17, 10, tzinfo=timezone.utc)


def test_missing_worker_database_is_unhealthy_without_creating_it(tmp_path):
    path = tmp_path / "missing.sqlite3"
    assert not is_healthy(path, now=NOW)
    assert not path.exists()


@pytest.mark.parametrize("stamp,healthy", [
    (NOW.isoformat(), True),
    ((NOW - timedelta(seconds=181)).isoformat(), False),
    ((NOW + timedelta(minutes=5)).isoformat(), False),
    ("not-a-date", False),
    ("2026-09-17T10:00:00", False),
])
def test_worker_health_requires_recent_valid_progress(tmp_path, stamp, healthy):
    store = WorkspaceStore(tmp_path / "workspace.sqlite3")
    with store.transaction() as db:
        db.execute("INSERT INTO ws_worker VALUES('service',?,'test')", (stamp,))
    assert is_healthy(store.path, now=NOW) is healthy


def test_browser_activity_cannot_mask_a_stopped_worker(tmp_path):
    store = WorkspaceStore(tmp_path / "workspace.sqlite3")
    with store.transaction() as db:
        db.executemany("INSERT INTO ws_worker VALUES(?,?,'manual')",
                       [(name, NOW.isoformat()) for name in ("scheduler", "sender")])
    assert not is_healthy(store.path, now=NOW)


def test_worker_command_reports_its_own_progress(tmp_path):
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / "workspace.sqlite3"
    environment = {**os.environ, "APP_MODE": "local", "SPACE_ID": "",
                   "WORKSPACE_DATABASE_PATH": str(database), "WORKSPACE_LIVE_ENABLED": "false",
                   "WORKSPACE_MASTER_KEY": ""}
    result = subprocess.run([sys.executable, str(root / "worker.py"), "--once"],
                            env=environment, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert is_healthy(database)
