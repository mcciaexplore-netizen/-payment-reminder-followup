"""Local SQLite, SQLite Cloud and libSQL, without temporary-file fallbacks."""
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
import config


class StorageConfigurationError(ValueError):
    """An operator must finish configuring durable workspace storage."""


def is_remote(location):
    return isinstance(location, str) and "://" in location


def validate_remote_url(url):
    try:
        parsed = urlsplit(url)
        if (any(character.isspace() for character in url) or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or not re.fullmatch(r"[A-Za-z0-9.-]+", parsed.hostname)):
            raise ValueError()
        if parsed.scheme == "sqlitecloud":
            if (not re.fullmatch(r"/[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", parsed.path)
                    or parsed.port == 0):
                raise ValueError()
        elif (parsed.scheme not in {"libsql", "https"}
                or parsed.path not in {"", "/"} or parsed.port not in {None, 443}):
            raise ValueError()
    except ValueError:
        raise StorageConfigurationError(
            "Use sqlitecloud://HOST:8860/DATABASE or a libsql:// (or HTTPS) database hostname. "
            "Put the API key in WORKSPACE_DATABASE_TOKEN, not in the URL. Dashboard links are not database URLs."
        ) from None
    return url.rstrip("/")


def database_location():
    url = os.getenv("WORKSPACE_DATABASE_URL", "").strip() or os.getenv("TURSO_DATABASE_URL", "").strip()
    if url:
        return validate_remote_url(url)
    if os.getenv("VERCEL") == "1":
        raise StorageConfigurationError("Configure a hosted SQLite database for this Vercel deployment. Local database files are not persistent here.")
    value = os.getenv("WORKSPACE_DATABASE_PATH", "").strip()
    path = Path(value).expanduser() if value else config.ROOT / ".data" / "workspace.sqlite3"
    return path if path.is_absolute() else config.ROOT / path


def remote_credentials(url=None):
    cloud = bool(url and urlsplit(url).scheme == "sqlitecloud")
    token = os.getenv("WORKSPACE_DATABASE_TOKEN", "").strip()
    if not token and not cloud:
        token = os.getenv("TURSO_AUTH_TOKEN", "").strip()
    if not token:
        raise StorageConfigurationError("Configure the hosted SQLite access token.")
    if cloud and not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", token):
        raise StorageConfigurationError("Configure a valid SQLite Cloud API key in WORKSPACE_DATABASE_TOKEN.")
    key = os.getenv("WORKSPACE_MASTER_KEY", "")
    try:
        Fernet(key.encode("ascii"))
    except (ValueError, UnicodeError):
        raise StorageConfigurationError("Configure a valid, permanent WORKSPACE_MASTER_KEY for saved integration credentials.") from None
    return token


class _Row(tuple):
    def __new__(cls, values, columns):
        row = super().__new__(cls, values)
        row.columns = columns
        return row

    def keys(self):
        return self.columns

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                key = next(i for i, column in enumerate(self.columns) if column.casefold() == key.casefold())
            except StopIteration:
                raise IndexError("No item with that key") from None
        return super().__getitem__(key)


def _call(operation, *args):
    import libsql
    try:
        return operation(*args)
    except libsql.Error:
        # Driver errors may contain URLs or SQL values. Do not expose them in UI/logs.
        # Never retry automatically: a lost response may follow a committed write.
        raise sqlite3.OperationalError("Hosted SQLite operation failed. Check database access and service availability.") from None


class _Cursor:
    def __init__(self, cursor, call=_call):
        self._cursor = cursor
        self._call = call
        self.description = cursor.description
        self.rowcount = cursor.rowcount
        self.lastrowid = cursor.lastrowid
        self._columns = tuple(column[0] for column in (self.description or ()))

    def fetchone(self):
        row = self._call(self._cursor.fetchone)
        return _Row(row, self._columns) if row is not None else None

    def fetchall(self):
        return [_Row(row, self._columns) for row in self._call(self._cursor.fetchall)]

    def __iter__(self):
        return self

    def __next__(self):
        row = self.fetchone()
        if row is None:
            raise StopIteration
        return row


class RemoteConnection:
    """The small sqlite3 connection interface used by workspace services."""
    _call = staticmethod(_call)

    def __init__(self, url, token):
        import libsql
        try:
            # Direct remote access: no local replica, sync file, or cached database.
            self._connection = libsql.connect(database=url, auth_token=token,
                                              isolation_level=None, timeout=10)
        except libsql.Error:
            raise sqlite3.OperationalError("Could not connect to hosted SQLite. Check database configuration.") from None

    def execute(self, sql, parameters=()):
        return _Cursor(self._call(self._connection.execute, sql, tuple(parameters)), self._call)

    def executemany(self, sql, parameters):
        return _Cursor(self._call(self._connection.executemany, sql, [tuple(row) for row in parameters]), self._call)

    def executescript(self, script):
        self._call(self._connection.executescript, script)

    def commit(self):
        self._call(self._connection.commit)

    def rollback(self):
        self._call(self._connection.rollback)

    def close(self):
        self._call(self._connection.close)


class SQLiteCloudConnection(RemoteConnection):
    """SQLite Cloud's DB-API needs explicit SQL for scripts and transaction ends."""

    def __init__(self, url, token):
        import sqlitecloud
        from sqlitecloud.datatypes import SQLiteCloudAccount, SQLiteCloudConfig

        parsed = urlsplit(validate_remote_url(url))
        account = SQLiteCloudAccount(hostname=parsed.hostname, port=parsed.port or 8860,
                                     dbname=parsed.path[1:], apikey=token)
        settings = SQLiteCloudConfig()
        settings.connect_timeout = 10
        settings.timeout = 10
        # TLS and certificate verification stay enabled. No in-memory database,
        # automatic creation or non-linearizable reads, even on serverless hosts.
        self._connection = self._call(sqlitecloud.connect, account, settings)

    @staticmethod
    def _call(operation, *args):
        import sqlitecloud
        try:
            return operation(*args)
        except (sqlitecloud.Error, OSError):
            # Do not retry an operation whose commit outcome may be uncertain.
            raise sqlite3.OperationalError(
                "SQLite Cloud operation failed. Check database access and service availability."
            ) from None

    def executescript(self, script):
        # SDK 0.0.84 lacks executescript(), but execute() accepts SQL batches.
        self.execute(script)

    def commit(self):
        # SDK 0.0.84 commit()/rollback() suppress OperationalError. Financial
        # writes must never appear successful when the server rejected COMMIT.
        self.execute("COMMIT")

    def rollback(self):
        self.execute("ROLLBACK")


def connect_remote(url):
    url = validate_remote_url(url)
    connection = SQLiteCloudConnection if urlsplit(url).scheme == "sqlitecloud" else RemoteConnection
    return connection(url, remote_credentials(url))
