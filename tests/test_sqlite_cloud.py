from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch
import sqlite3

from cryptography.fernet import Fernet
import pytest
import sqlitecloud

from workspace_storage import (SQLiteCloudConnection, StorageConfigurationError,
                               connect_remote, remote_credentials, validate_remote_url)
from workspace_store import WorkspaceStore


URL = "sqlitecloud://test.sqlite.cloud:8860/payment_reminder"


@pytest.mark.parametrize("url", [
    "sqlitecloud://test.sqlite.cloud", "sqlitecloud://test.sqlite.cloud/",
    URL + "?apikey=private-secret", URL + "?insecure=true",
    URL + "#private-secret", URL + "/extra", URL + ";DROP",
    "sqlitecloud://test.sqlite.cloud:99999/db", "sqlitecloud://test.sqlite.cloud:0/db",
    "sqlitecloud://test.sqlite.cloud/db%3Bbad", "sqlitecloud://test.sqlite.cloud/db\n",
    "https://dashboard.sqlitecloud.io/organizations/private-secret/projects",
])
def test_cloud_rejects_invalid_or_unsafe_connection_urls(url):
    with pytest.raises(StorageConfigurationError) as error:
        validate_remote_url(url)
    assert "private-secret" not in str(error.value)


def test_cloud_uses_its_own_api_key_not_an_unrelated_turso_token(monkeypatch):
    monkeypatch.delenv("WORKSPACE_DATABASE_TOKEN", raising=False)
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "not-a-cloud-key")
    monkeypatch.setenv("WORKSPACE_MASTER_KEY", Fernet.generate_key().decode())
    with pytest.raises(StorageConfigurationError, match="access token"):
        remote_credentials(URL)
    monkeypatch.setenv("WORKSPACE_DATABASE_TOKEN", "private-key; USE DATABASE other;")
    with pytest.raises(StorageConfigurationError, match="valid SQLite Cloud API key"):
        remote_credentials(URL)


def test_cloud_schema_rows_batches_and_rollback_survive_reconnection(monkeypatch, sqlite_cloud_server):
    monkeypatch.setenv("WORKSPACE_DATABASE_TOKEN", "test-api-key")
    monkeypatch.setenv("WORKSPACE_MASTER_KEY", Fernet.generate_key().decode())
    with patch.object(Path, "mkdir", side_effect=AssertionError("unexpected local directory")):
        store = WorkspaceStore(URL)
        assert store.remote and store.key_directory is None
        with store.transaction() as db:
            assert isinstance(db, SQLiteCloudConnection)
            db.execute("CREATE TABLE adapter_probe (id INTEGER PRIMARY KEY, value TEXT)")
            db.executemany("INSERT INTO adapter_probe(value) VALUES (?)", [("one;two",), ("₹ नमस्ते",)])
        with pytest.raises(RuntimeError):
            with store.transaction() as db:
                db.execute("DELETE FROM adapter_probe")
                raise RuntimeError("abort")
        with closing(connect_remote(URL)) as db:
            rows = list(db.execute("SELECT * FROM adapter_probe ORDER BY id"))
            assert dict(rows[0]) == {"id": 1, "value": "one;two"}
            assert rows[1]["VALUE"] == "₹ नमस्ते"
            assert rows[1][0] == 2
    for settings in sqlite_cloud_server.configs:
        assert settings.account.dbname == "payment_reminder"
        assert settings.account.hostname == "test.sqlite.cloud"
        assert settings.account.port == 8860
        assert settings.account.apikey == "test-api-key"
        assert settings.connect_timeout == settings.timeout == 10
        assert not any((settings.insecure, settings.no_verify_certificate,
                        settings.create, settings.memory, settings.non_linearizable))


@pytest.mark.parametrize("operation", ["COMMIT", "ROLLBACK"])
def test_cloud_transaction_failure_is_reported_once_not_silently_ignored(sqlite_cloud_server, operation):
    with closing(SQLiteCloudConnection(URL, "test-key")) as db:
        db.execute("BEGIN IMMEDIATE")
        sqlite_cloud_server.fail_on = operation
        with pytest.raises(sqlite3.OperationalError) as error:
            getattr(db, operation.lower())()
        assert "private-server-error" not in str(error.value)
        assert sqlite_cloud_server.statements.count(operation) == 1


@pytest.mark.parametrize("failure", [sqlitecloud.Error("private-key"), OSError("private-host")])
def test_cloud_connect_failure_is_redacted_and_not_retried(monkeypatch, failure):
    connect = Mock(side_effect=failure)
    monkeypatch.setattr(sqlitecloud, "connect", connect)
    with pytest.raises(sqlite3.OperationalError) as error:
        SQLiteCloudConnection(URL, "private-key")
    assert "private" not in str(error.value)
    assert connect.call_count == 1
