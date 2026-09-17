from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from unittest.mock import Mock
import sqlite3

from cryptography.fernet import Fernet
import psycopg
import pytest

from accounts import Accounts
from postgres_storage import PostgresConnection, postgres_sql, validate_postgres_url
from workspace_storage import StorageConfigurationError, database_location, remote_credentials
from workspace_store import WorkspaceError, WorkspaceStore


URL = "postgresql://owner:private-password@ep-test-pooler.us-east-1.aws.neon.tech/payment_reminder?sslmode=require&channel_binding=require"


def test_neon_database_url_alias_and_master_key(monkeypatch):
    monkeypatch.delenv("WORKSPACE_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://unused.example")
    monkeypatch.delenv("WORKSPACE_DATABASE_TOKEN", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("WORKSPACE_MASTER_KEY", "")
    assert database_location() == URL
    with pytest.raises(StorageConfigurationError, match="WORKSPACE_MASTER_KEY"):
        remote_credentials(URL)
    monkeypatch.setenv("WORKSPACE_MASTER_KEY", Fernet.generate_key().decode())
    assert remote_credentials(URL) == ""
    monkeypatch.setenv("WORKSPACE_DATABASE_URL", "libsql://explicit.example")
    assert database_location() == "libsql://explicit.example"


@pytest.mark.parametrize("url", [
    URL.replace("sslmode=require", "sslmode=disable"),
    URL.replace("sslmode=require", "sslmode=prefer"),
    URL + "&host=attacker.example", URL + "&sslmode=disable",
    URL + "&options=-csearch_path=public", URL + "#private-password",
    URL.replace("owner:private-password@", ""),
    URL.replace("/payment_reminder?", "/?"),
    URL.replace("/payment_reminder?", "/wrong/path?"),
    URL.replace(".tech/", ".tech:99999/"), URL + "\n",
])
def test_invalid_neon_settings_fail_without_echoing_credentials(url):
    with pytest.raises(StorageConfigurationError) as error:
        validate_postgres_url(url)
    assert "private-password" not in str(error.value)


def test_postgres_parameters_do_not_change_literals_or_comments():
    source = "SELECT ?, 'why? 100% it''s safe', \"?\" /* ? */ -- ?\n, ?"
    assert postgres_sql(source) == "SELECT $1, 'why? 100% it''s safe', \"?\" /* ? */ -- ?\n, $2"


def test_connection_uses_pooler_safe_options_and_no_silent_retry(monkeypatch):
    connect = Mock(side_effect=psycopg.OperationalError("private-password"))
    monkeypatch.setattr(psycopg, "connect", connect)
    with pytest.raises(sqlite3.OperationalError) as error:
        PostgresConnection(URL)
    assert "private-password" not in str(error.value)
    assert connect.call_count == 1
    assert connect.call_args.kwargs["sslmode"] == "require"
    assert connect.call_args.kwargs["prepare_threshold"] is None
    assert connect.call_args.kwargs["connect_timeout"] == 10


def test_failed_postgres_commit_is_not_retried_or_reported_successful(monkeypatch):
    underlying = Mock()
    underlying.commit.side_effect = psycopg.OperationalError("private-password")
    monkeypatch.setattr(psycopg, "connect", Mock(return_value=underlying))
    with closing(PostgresConnection(URL)) as db:
        with pytest.raises(sqlite3.OperationalError) as error:
            db.commit()
    assert "private-password" not in str(error.value)
    assert underlying.commit.call_count == 1


def test_postgres_real_rows_batches_rollback_and_large_money(postgres_server, monkeypatch):
    monkeypatch.setenv("WORKSPACE_MASTER_KEY", Fernet.generate_key().decode())
    store = WorkspaceStore(postgres_server)
    assert store.remote and store.key_directory is None
    with store.transaction() as db:
        db.execute("INSERT INTO ws_installments VALUES(?,?,?,?,?)", ("b", "i", 1, "2026-09-01", 5_000_000_000))
        db.executemany("INSERT INTO ws_installments VALUES(?,?,?,?,?)", [("b", "i", 2, "2026-10-01", 1), ("b", "i", 3, "2026-11-01", 2)])
    with pytest.raises(RuntimeError):
        with store.transaction() as db:
            db.execute("DELETE FROM ws_installments")
            raise RuntimeError("rollback")
    with closing(WorkspaceStore(postgres_server).connect()) as db:
        rows = list(db.execute("SELECT amount FROM ws_installments ORDER BY sequence"))
        assert dict(rows[0]) == {"amount": 5_000_000_000}
        assert rows[0]["AMOUNT"] == rows[0][0]
        assert len(rows) == 3
        assert db.execute("SELECT ? AS value", ("O'Brien ? 100% ₹ नमस्ते",)).fetchone()[0] == "O'Brien ? 100% ₹ नमस्ते"


def test_postgres_first_owner_creation_is_atomic(postgres_server, monkeypatch):
    monkeypatch.setenv("WORKSPACE_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("WORKSPACE_SETUP_TOKEN", "x" * 40)
    store = WorkspaceStore(postgres_server)

    def bootstrap(index):
        try:
            Accounts(store).bootstrap(f"owner{index}@example.com", "a long account password", "Test", setup_token="x" * 40)
            return True
        except WorkspaceError as error:
            assert "already" in str(error)
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(bootstrap, [1, 2])) == [False, True]
    with closing(store.connect()) as db:
        assert db.execute("SELECT count(*) FROM ws_users").fetchone()[0] == 1
