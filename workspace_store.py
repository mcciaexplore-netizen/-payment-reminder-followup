"""Versioned workspace persistence. All business mutations use one SQLite transaction."""
import json
from contextlib import closing
from datetime import datetime, timezone
from reminders import ReminderStore


def utcnow():
    return datetime.now(timezone.utc)


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class WorkspaceError(ValueError):
    pass


class WorkspaceStore(ReminderStore):
    def __init__(self, path):
        super().__init__(path)
        with closing(self.connect()) as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS ws_schema(version INTEGER PRIMARY KEY);
            INSERT OR IGNORE INTO ws_schema VALUES(1);
            CREATE TABLE IF NOT EXISTS ws_users(
                id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, password TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ws_sessions(
                token TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ws_login_failures(
                email TEXT PRIMARY KEY, failures INTEGER NOT NULL, since TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ws_businesses(
                id TEXT PRIMARY KEY, name TEXT NOT NULL, timezone TEXT NOT NULL,
                created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ws_members(
                business_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL,
                PRIMARY KEY(business_id,user_id));
            CREATE TABLE IF NOT EXISTS ws_invites(
                token TEXT PRIMARY KEY, business_id TEXT NOT NULL, email TEXT NOT NULL,
                role TEXT NOT NULL, expires TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS ws_invoices(
                business_id TEXT NOT NULL, invoice_key TEXT NOT NULL, data TEXT NOT NULL,
                profile TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY(business_id,invoice_key));
            CREATE TABLE IF NOT EXISTS ws_receipts(
                id TEXT PRIMARY KEY, business_id TEXT NOT NULL, invoice_key TEXT NOT NULL,
                amount INTEGER NOT NULL, kind TEXT NOT NULL, reference TEXT NOT NULL,
                received_on TEXT NOT NULL, created TEXT NOT NULL, actor TEXT NOT NULL,
                UNIQUE(business_id,reference));
            CREATE INDEX IF NOT EXISTS ws_receipt_invoice ON ws_receipts(business_id,invoice_key);
            CREATE TABLE IF NOT EXISTS ws_templates(
                id TEXT PRIMARY KEY, business_id TEXT NOT NULL, name TEXT NOT NULL,
                language TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ws_rules(
                id TEXT PRIMARY KEY, business_id TEXT NOT NULL, name TEXT NOT NULL,
                settings TEXT NOT NULL, active INTEGER NOT NULL, created_by TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ws_jobs(
                id TEXT PRIMARY KEY, business_id TEXT NOT NULL, invoice_key TEXT NOT NULL,
                rule_id TEXT, unique_key TEXT UNIQUE NOT NULL, channel TEXT NOT NULL,
                mode TEXT NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL,
                due_at TEXT NOT NULL, created TEXT NOT NULL, attempted_at TEXT,
                completed_at TEXT, provider_id TEXT, detail TEXT NOT NULL DEFAULT '',
                authorized_by TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS ws_job_due ON ws_jobs(state,due_at);
            CREATE INDEX IF NOT EXISTS ws_job_invoice ON ws_jobs(business_id,invoice_key,channel,mode,state);
            CREATE TABLE IF NOT EXISTS ws_connectors(
                business_id TEXT NOT NULL, kind TEXT NOT NULL, endpoint TEXT NOT NULL,
                secret TEXT NOT NULL, enabled INTEGER NOT NULL,
                PRIMARY KEY(business_id,kind));
            CREATE TABLE IF NOT EXISTS ws_links(
                id TEXT PRIMARY KEY, business_id TEXT NOT NULL, invoice_key TEXT NOT NULL,
                amount INTEGER NOT NULL, currency TEXT NOT NULL, state TEXT NOT NULL,
                provider_id TEXT, url TEXT, created TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS ws_link_provider ON ws_links(business_id,provider_id);
            CREATE TABLE IF NOT EXISTS ws_events(
                business_id TEXT NOT NULL, event_id TEXT NOT NULL, received TEXT NOT NULL,
                result TEXT NOT NULL, PRIMARY KEY(business_id,event_id));
            CREATE TABLE IF NOT EXISTS ws_audit(
                id INTEGER PRIMARY KEY, business_id TEXT NOT NULL, actor TEXT NOT NULL,
                action TEXT NOT NULL, detail TEXT NOT NULL, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ws_worker(
                id TEXT PRIMARY KEY, heartbeat TEXT NOT NULL, detail TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ws_installments(
                business_id TEXT NOT NULL, invoice_key TEXT NOT NULL, sequence INTEGER NOT NULL,
                due_date TEXT NOT NULL, amount INTEGER NOT NULL,
                PRIMARY KEY(business_id,invoice_key,sequence));
            CREATE TABLE IF NOT EXISTS ws_portals(
                token TEXT PRIMARY KEY, business_id TEXT NOT NULL, email TEXT NOT NULL,
                expires TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS ws_branding(
                business_id TEXT PRIMARY KEY, settings TEXT NOT NULL);
            """)

    @staticmethod
    def audit(db, business, actor, action, detail, now):
        db.execute("INSERT INTO ws_audit(business_id,actor,action,detail,created) VALUES(?,?,?,?,?)",
                   (business, actor, action, encode(detail), now.isoformat()))
