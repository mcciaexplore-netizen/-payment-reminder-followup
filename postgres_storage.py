"""PostgreSQL adapter for the workspace's small SQLite-style connection API."""
import re
import sqlite3
from urllib.parse import parse_qsl, urlsplit

import psycopg

from workspace_storage import StorageConfigurationError, _Row


# Database-scoped transaction lock, shared by every app instance/worker. This
# deliberately preserves SQLite's single-writer semantics for read/check/write
# operations (first owner, receipts, reminder claims), including empty tables.
WORKSPACE_WRITE_LOCK = 0x4D434349415753


def validate_postgres_url(url):
    try:
        parsed = urlsplit(url)
        options = parse_qsl(parsed.query, strict_parsing=True)
        settings = dict(options)
        if (parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname
                or not parsed.username or not parsed.password or parsed.fragment
                or not parsed.path.strip("/") or "/" in parsed.path[1:]
                or any(c.isspace() or ord(c) < 32 for c in url)
                or parsed.port == 0 or len(options) != len(settings)
                or set(settings) - {"sslmode", "channel_binding"}):
            raise ValueError()
        sslmode = settings.get("sslmode", "require")
        if sslmode not in {"require", "verify-ca", "verify-full"}:
            if not (sslmode == "disable" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
                raise ValueError()
        if settings.get("channel_binding", "prefer") not in {"require", "prefer"}:
            raise ValueError()
    except (TypeError, ValueError):
        raise StorageConfigurationError(
            "Use the Neon PostgreSQL connection string including its username, password and database. "
            "Keep sslmode=require (or stronger) and save the whole string as a private environment variable."
        ) from None
    return url


def postgres_sql(sql):
    """Translate positional markers outside SQL literals, identifiers/comments.

    RawCursor uses $1 markers, so literal percent signs need no escaping.
    Values are always sent separately to PostgreSQL, never interpolated.
    """
    tokens = re.compile(r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|--[^\n]*(?:\n|$)|/\*.*?\*/|\?)", re.S)
    index = 0

    def marker(match):
        nonlocal index
        if match[0] != "?":
            return match[0]
        index += 1
        return f"${index}"

    return tokens.sub(marker, sql)


def _call(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except (psycopg.Error, OSError):
        # No retries: the server may have committed even if its reply was lost.
        raise sqlite3.OperationalError(
            "Hosted PostgreSQL operation failed. Check database access and service availability."
        ) from None


class PostgresCursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self.description = cursor.description
        self.rowcount = cursor.rowcount
        self.lastrowid = None
        self._columns = tuple(column.name for column in (cursor.description or ()))

    def fetchone(self):
        row = _call(self._cursor.fetchone)
        return _Row(row, self._columns) if row is not None else None

    def fetchall(self):
        return [_Row(row, self._columns) for row in _call(self._cursor.fetchall)]

    def __iter__(self):
        return self

    def __next__(self):
        row = self.fetchone()
        if row is None:
            raise StopIteration
        return row


class PostgresConnection:
    def __init__(self, url):
        url = validate_postgres_url(url)
        sslmode = dict(parse_qsl(urlsplit(url).query)).get("sslmode", "require")
        self._connection = _call(psycopg.connect, url, autocommit=True,
                                 cursor_factory=psycopg.RawCursor, prepare_threshold=None,
                                 connect_timeout=10, sslmode=sslmode)

    def _begin_write(self):
        _call(self._connection.execute, "BEGIN ISOLATION LEVEL READ COMMITTED")
        try:
            _call(self._connection.execute, "SET LOCAL lock_timeout = '10s'")
            _call(self._connection.execute, "SET LOCAL statement_timeout = '30s'")
            return _call(self._connection.execute, "SELECT pg_advisory_xact_lock($1)", (WORKSPACE_WRITE_LOCK,))
        except BaseException:
            _call(self._connection.rollback)
            raise

    def execute(self, sql, parameters=()):
        if sql.strip().rstrip(";").upper() == "BEGIN IMMEDIATE":
            return PostgresCursor(self._begin_write())
        return PostgresCursor(_call(self._connection.execute, postgres_sql(sql), tuple(parameters) or None))

    def executemany(self, sql, parameters):
        cursor = self._connection.cursor()
        _call(cursor.executemany, postgres_sql(sql), [tuple(row) for row in parameters])
        return PostgresCursor(cursor)

    def executescript(self, script):
        # Only the trusted, checked-in schema uses this API. PostgreSQL needs an
        # identity for generated audit IDs and BIGINT for SQLite's 64-bit money.
        script = re.sub(r"\bid INTEGER PRIMARY KEY\b", "id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY", script)
        script = re.sub(r"\bINTEGER\b", "BIGINT", script)
        self._begin_write()
        try:
            _call(self._connection.execute, script)
            self.commit()
        except BaseException:
            self.rollback()
            raise

    def commit(self):
        _call(self._connection.commit)

    def rollback(self):
        _call(self._connection.rollback)

    def close(self):
        _call(self._connection.close)
