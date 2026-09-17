"""Local SQLite and remote libSQL storage, without temporary-file fallbacks."""
import os
from pathlib import Path
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
        if (parsed.scheme not in {"libsql", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {"", "/"} or parsed.port not in {None, 443}):
            raise ValueError()
    except ValueError:
        raise StorageConfigurationError("Use a libsql:// or HTTPS database hostname without credentials or a path.") from None
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


def remote_credentials():
    token = os.getenv("WORKSPACE_DATABASE_TOKEN", "") or os.getenv("TURSO_AUTH_TOKEN", "")
    if not token:
        raise StorageConfigurationError("Configure the hosted SQLite access token.")
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
    def __init__(self, cursor):
        self._cursor = cursor
        self.description = cursor.description
        self.rowcount = cursor.rowcount
        self.lastrowid = cursor.lastrowid
        self._columns = tuple(column[0] for column in self.description)

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


class RemoteConnection:
    """The small sqlite3 connection interface used by workspace services."""
    def __init__(self, url, token):
        import libsql
        try:
            # Direct remote access: no local replica, sync file, or cached database.
            self._connection = libsql.connect(database=url, auth_token=token,
                                              isolation_level=None, timeout=10)
        except libsql.Error:
            raise sqlite3.OperationalError("Could not connect to hosted SQLite. Check database configuration.") from None

    def execute(self, sql, parameters=()):
        return _Cursor(_call(self._connection.execute, sql, tuple(parameters)))

    def executemany(self, sql, parameters):
        return _Cursor(_call(self._connection.executemany, sql, [tuple(row) for row in parameters]))

    def executescript(self, script):
        _call(self._connection.executescript, script)

    def commit(self):
        _call(self._connection.commit)

    def rollback(self):
        _call(self._connection.rollback)

    def close(self):
        _call(self._connection.close)


def connect_remote(url):
    return RemoteConnection(validate_remote_url(url), remote_credentials())
