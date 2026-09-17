from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch
import sqlite3

import libsql
import pytest
from cryptography.fernet import Fernet
from starlette.testclient import TestClient

from accounts import Accounts
import config
from connectors import Vault
from ledger import Ledger
from webhooks import create_app
from workspace_storage import (StorageConfigurationError, RemoteConnection,
                               database_location, remote_credentials)
from workspace_store import WorkspaceError, WorkspaceStore
from worker_health import is_healthy


@pytest.fixture
def storage_env(monkeypatch):
    for key in ("WORKSPACE_DATABASE_URL", "TURSO_DATABASE_URL", "WORKSPACE_DATABASE_TOKEN",
                "TURSO_AUTH_TOKEN", "WORKSPACE_MASTER_KEY", "WORKSPACE_SETUP_TOKEN", "VERCEL",
                "WORKSPACE_DATABASE_PATH", "CRON_SECRET", "WORKSPACE_SCHEDULER_MODE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, "IS_DEMO", False)
    return monkeypatch


def test_vercel_never_falls_back_to_a_local_or_temporary_file(storage_env):
    storage_env.setenv("VERCEL", "1")
    storage_env.setenv("WORKSPACE_DATABASE_PATH", "/tmp/workspace.sqlite3")
    with pytest.raises(StorageConfigurationError, match="hosted SQLite"):
        database_location()


def test_local_relative_storage_is_resolved_from_the_project(storage_env, tmp_path):
    storage_env.chdir(tmp_path)
    storage_env.setenv("WORKSPACE_DATABASE_PATH", ".data/workspace.sqlite3")
    assert database_location() == config.ROOT / ".data/workspace.sqlite3"
    storage_env.setenv("WORKSPACE_DATABASE_PATH", "")
    assert database_location() == config.ROOT / ".data/workspace.sqlite3"


@pytest.mark.parametrize("url", ["http://database.example", "file:///tmp/db", "https://name:secret@db.example", "https://db.example/?token=secret"])
def test_invalid_remote_location_is_rejected_without_echoing_secrets(storage_env, url):
    storage_env.setenv("WORKSPACE_DATABASE_URL", url)
    with pytest.raises(StorageConfigurationError) as result:
        database_location()
    assert "secret" not in str(result.value)


def test_turso_aliases_and_permanent_key_are_required(storage_env):
    storage_env.setenv("TURSO_DATABASE_URL", "libsql://test.example")
    assert database_location() == "libsql://test.example"
    with pytest.raises(StorageConfigurationError, match="access token"):
        remote_credentials()
    storage_env.setenv("TURSO_AUTH_TOKEN", "test-auth-token")
    with pytest.raises(StorageConfigurationError, match="WORKSPACE_MASTER_KEY"):
        remote_credentials()
    storage_env.setenv("WORKSPACE_MASTER_KEY", Fernet.generate_key().decode())
    assert remote_credentials() == "test-auth-token"


def test_remote_workspace_survives_reconnection_without_application_file_writes(storage_env, tmp_path):
    url = "libsql://test.example"
    storage_env.setenv("WORKSPACE_DATABASE_TOKEN", "test-token")
    storage_env.setenv("WORKSPACE_MASTER_KEY", Fernet.generate_key().decode())
    storage_env.setenv("WORKSPACE_SETUP_TOKEN", "private-setup-code-" * 3)
    real_connect = libsql.connect
    calls = []

    def simulated_server(**kwargs):
        calls.append(kwargs)
        assert kwargs["database"] == url
        assert kwargs["auth_token"] == "test-token"
        # Actual libSQL driver; only the network boundary is replaced.
        return real_connect(str(tmp_path / "hosted.sqlite3"), isolation_level=None)

    storage_env.setattr(libsql, "connect", simulated_server)
    with patch.object(Path, "mkdir", side_effect=AssertionError("remote storage wrote a local folder")):
        store = WorkspaceStore(url)
        accounts = Accounts(store)
        with pytest.raises(WorkspaceError, match="setup code"):
            accounts.bootstrap("owner@example.com", "a long account password", "Test")
        token, business = accounts.bootstrap("owner@example.com", "a long account password", "Test", setup_token="private-setup-code-" * 3)
        ledger = Ledger(store, token, business)
        ledger.import_records([dict(invoice_no="SQL-1", client_name="Test", email="customer@example.com",
            amount="125.00", amount_paid="0", due_date="2026-09-01", status="unpaid", currency="INR")])
        with pytest.raises(RuntimeError):
            with store.transaction() as db:
                db.execute("DELETE FROM ws_invoices")
                raise RuntimeError("roll back")
        reopened = WorkspaceStore(url)
        assert Ledger(reopened, token, business).invoices()[0]["invoice_no"] == "SQL-1"
        assert reopened.key_directory is None
        vault = Vault(reopened.key_directory)
        assert vault.open(vault.seal({"secret": "preserved"})) == {"secret": "preserved"}
    assert calls


def test_driver_connection_errors_are_redacted_and_not_retried(storage_env):
    connect = Mock(side_effect=libsql.Error("private-token and private-database-url"))
    storage_env.setattr(libsql, "connect", connect)
    with pytest.raises(sqlite3.OperationalError) as result:
        RemoteConnection("libsql://test.example", "private-token")
    assert "private" not in str(result.value)
    assert connect.call_count == 1


def test_commit_failure_is_not_retried_or_hidden(storage_env):
    underlying = Mock()
    underlying.commit.side_effect = libsql.Error("private-provider-detail")
    storage_env.setattr(libsql, "connect", Mock(return_value=underlying))
    with closing(RemoteConnection("libsql://test.example", "token")) as db:
        with pytest.raises(sqlite3.OperationalError) as result:
            db.commit()
    assert "private-provider-detail" not in str(result.value)
    assert underlying.commit.call_count == 1


@pytest.mark.parametrize("secret,header,mode", [("", "", "cron"), ("x" * 32, "", "cron"), ("x" * 32, "Bearer wrong", "cron"), ("x" * 32, "Bearer " + "x" * 32, "service")])
def test_scheduler_rejects_unauthorized_calls_before_opening_storage(storage_env, secret, header, mode):
    storage_env.setenv("CRON_SECRET", secret)
    storage_env.setenv("WORKSPACE_SCHEDULER_MODE", mode)
    with patch("scheduled_tasks._run_pass") as run:
        response = TestClient(create_app()).get("/api/reminders/tick", headers={"Authorization": header})
    assert response.status_code == 401
    run.assert_not_called()


def test_scheduler_reports_real_progress_and_keeps_browser_health_separate(storage_env, tmp_path):
    path = tmp_path / "cron.sqlite3"
    storage_env.setenv("WORKSPACE_DATABASE_PATH", str(path))
    storage_env.setenv("CRON_SECRET", "x" * 32)
    storage_env.setenv("WORKSPACE_SCHEDULER_MODE", "cron")
    storage_env.setenv("WORKSPACE_LIVE_ENABLED", "false")
    response = TestClient(create_app()).get("/api/reminders/tick", headers={"Authorization": "Bearer " + "x" * 32})
    assert response.status_code == 200
    assert response.json() == {"processed": 0}
    assert response.headers["cache-control"] == "no-store"
    assert is_healthy(path, service="cron", max_age=26 * 3600)
    assert not is_healthy(path)


def test_failed_scheduler_does_not_report_success_or_echo_database_details(storage_env):
    storage_env.setenv("CRON_SECRET", "x" * 32)
    storage_env.setenv("WORKSPACE_SCHEDULER_MODE", "cron")
    with patch("scheduled_tasks._run_pass", side_effect=sqlite3.OperationalError("private-database-detail")):
        response = TestClient(create_app()).get("/api/reminders/tick", headers={"Authorization": "Bearer " + "x" * 32})
    assert response.status_code == 503
    assert "private-database-detail" not in response.text


def test_vercel_startup_explains_missing_hosted_storage_without_an_exception(storage_env):
    from streamlit.testing.v1 import AppTest
    storage_env.setenv("VERCEL", "1")
    app = AppTest.from_file(config.ROOT / "app.py", default_timeout=30).run()
    assert not app.exception
    assert any("storage setup is incomplete" in error.value for error in app.error)
    assert any("hosted SQLite" in info.value for info in app.info)
