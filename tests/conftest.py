import sys
from pathlib import Path
from datetime import datetime, timezone
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import SendSettings
from reminders import ReminderService, ReminderStore


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
def sqlite_cloud_server(tmp_path, monkeypatch):
    """Run the real Cloud DB-API against a simulated server, with real SQLite SQL.

    Only the transport is replaced; SDK cursors, binding, batches and results
    remain real. This is not a live cloud connectivity or protocol test.
    """
    import sqlite3
    from types import SimpleNamespace
    import sqlitecloud
    from sqlitecloud.datatypes import SQLiteCloudConnect
    from sqlitecloud.driver import Driver
    from sqlitecloud.resultset import (SQLITECLOUD_RESULT_TYPE as Kind,
                                       SQLiteCloudOperationResult, SQLiteCloudResult)

    state = SimpleNamespace(configs=[], statements=[], fail_on=None)

    class Socket:
        def __init__(self, db):
            self.db = db

        def sendall(self, value):
            assert value == b"", "Unexpected transport use"

        def close(self):
            self.db.close()

    def connect(driver, hostname, port, settings):
        state.configs.append(settings)
        connection = SQLiteCloudConnect()
        connection.config = settings
        # Match the shared workspace fixture's path so local snapshot tests can
        # inspect this simulated server's backing file as well.
        connection.socket = Socket(sqlite3.connect(tmp_path / "workspace.sqlite3", isolation_level=None, timeout=10))
        return connection

    def statements(script):
        pending = ""
        for char in script:
            pending += char
            if char == ";" and sqlite3.complete_statement(pending):
                yield pending.strip()
                pending = ""
        if pending.strip():
            yield pending.strip()

    def execute(driver, sql, parameters, connection):
        db = connection.socket.db
        state.statements.append(sql)
        if state.fail_on and sql.strip().upper() == state.fail_on:
            raise sqlitecloud.OperationalError("private-server-error", 10, 10)
        offset = 0
        for statement in statements(sql):
            # Workspace SQL uses positional bindings, never literal question marks.
            size = statement.count("?")
            try:
                cursor = db.execute(statement, parameters[offset:offset + size])
            except sqlite3.Error as exc:
                raise sqlitecloud.OperationalError(str(exc)) from None
            offset += size
        assert offset == len(parameters)
        if cursor.description is not None:
            rows = cursor.fetchall()
            result = SQLiteCloudResult(Kind.RESULT_ROWSET)
            result.colname = [col[0] for col in cursor.description]
            result.ncols = len(result.colname)
            result.nrows = len(rows)
            result.data = [value for row in rows for value in row]
            return result
        return SQLiteCloudOperationResult(SQLiteCloudResult(Kind.RESULT_ARRAY,
            [0, 0, cursor.lastrowid or 0, cursor.rowcount, db.total_changes, 1]))

    monkeypatch.setattr(Driver, "connect", connect)
    monkeypatch.setattr(Driver, "execute_statement", execute)
    return state
