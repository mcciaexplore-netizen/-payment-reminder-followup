import sys
import os
import secrets
from pathlib import Path
from datetime import datetime, timezone
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import SendSettings
from reminders import ReminderService, ReminderStore


import tempfile

def pytest_configure(config):
    basetemp = config.getoption("basetemp", None)
    if basetemp:
        Path(basetemp).mkdir(parents=True, exist_ok=True)
    elif sys.platform == "win32":
        win_temp = Path(tempfile.gettempdir()) / "py_workspace_temp"
        win_temp.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = str(win_temp)


@pytest.fixture
def invoice():
    return dict(invoice_no="INV-001", client_name="Test Customer", email="customer@example.com",
                amount="12500", amount_paid="2000", due_date="2026-08-01", status="Partially Paid", currency="INR")


@pytest.fixture
def service(tmp_path):
    return ReminderService(ReminderStore(tmp_path / "history.sqlite3"),
        SendSettings(business_id="business-a", sender="sender@example.com", password="test-secret", dry_run=False, preview_only=False),
        transport=lambda *args: None, clock=lambda: datetime(2026, 9, 15, tzinfo=timezone.utc))


@pytest.fixture
def postgres_server(monkeypatch):
    """An isolated schema on the explicit test server; never the app database."""
    import psycopg
    from psycopg import sql
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL is required for real PostgreSQL integration tests")
    schema = "test_workspace_" + secrets.token_hex(12)
    real_connect = psycopg.connect
    with real_connect(url, autocommit=True) as db:
        db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    def isolated_connect(*args, **kwargs):
        db = real_connect(*args, **kwargs)
        db.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        return db

    monkeypatch.setattr(psycopg, "connect", isolated_connect)
    try:
        yield url
    finally:
        # Only remove the randomly named schema created by this fixture.
        with real_connect(url, autocommit=True) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
