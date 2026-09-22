"""Durable approval and send workflow. A model never controls these transitions.

SQLite serializes claims before transport. Unknown delivery and interrupted claims
remain blocked for reconciliation; SMTP cannot provide exactly-once delivery.
"""
import csv
import io
import json
import os
import secrets
import smtplib
import sqlite3
import ssl
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

from config import SendSettings
from invoices import email_address, overdue_invoices, validate_records


class ReminderError(ValueError):
    pass


class DeliveryFailure(Exception):
    """Provider explicitly rejected the message, or submission never started."""


class DeliveryUnknown(Exception):
    """Submission started but its outcome cannot be proved."""


def validate_message(subject, body):
    if not isinstance(subject, str) or not subject.strip() or len(subject) > 200 or any(c in subject for c in "\r\n\x00"):
        raise ReminderError("Subject is required, must be one line, and at most 200 characters.")
    if not isinstance(body, str) or not body.strip() or len(body) > 10000 or "\x00" in body:
        raise ReminderError("Email body is required and must be at most 10,000 characters.")


def _smtp_submit(settings, recipient, subject, body, message_id):
    """No fallback/retry after submission starts: a lost response can mean sent."""
    message = EmailMessage()
    message["From"] = email_address(settings.sender)
    message["To"] = email_address(recipient)
    message["Subject"] = subject
    message["Message-ID"] = message_id
    message.set_content(body)
    try:
        smtp = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20, context=ssl.create_default_context())
        try:
            smtp.login(settings.sender, settings.password)
        except Exception:
            smtp.close()
            raise
    except Exception as exc:
        raise DeliveryFailure("Could not connect or authenticate with Gmail. Check the account and connection.") from exc
    try:
        refused = smtp.send_message(message)
        if refused:
            raise DeliveryFailure("Gmail refused the recipient.")
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError) as exc:
        raise DeliveryFailure("Gmail rejected this message. Verify the recipient and account before retrying.") from exc
    except DeliveryFailure:
        raise
    except Exception as exc:
        raise DeliveryUnknown("Gmail's submission result is unknown. Check Sent mail before any retry.") from exc
    finally:
        # A QUIT failure after successful DATA must not turn success into a retry.
        try:
            smtp.close()
        except Exception:
            pass


class ReminderStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._initialize_schema()
        if os.name != "nt":
            self.path.chmod(0o600)

    def _initialize_schema(self):
        with closing(self.connect()) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS invoices (
                    business_id TEXT NOT NULL, invoice_key TEXT NOT NULL,
                    data TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY (business_id, invoice_key)
                );
                CREATE TABLE IF NOT EXISTS reminders (
                    id TEXT PRIMARY KEY, business_id TEXT NOT NULL, invoice_key TEXT NOT NULL,
                    invoice_data TEXT NOT NULL, sender TEXT NOT NULL, recipient TEXT NOT NULL,
                    subject TEXT NOT NULL, body TEXT NOT NULL, mode TEXT NOT NULL,
                    state TEXT NOT NULL, approved_at TEXT NOT NULL, attempted_at TEXT,
                    completed_at TEXT, error TEXT NOT NULL DEFAULT '', message_id TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS reminder_lookup
                    ON reminders (business_id, invoice_key, mode, state, attempted_at);
                CREATE TABLE IF NOT EXISTS reconciliations (
                    id INTEGER PRIMARY KEY, reminder_id TEXT NOT NULL, business_id TEXT NOT NULL,
                    previous_state TEXT NOT NULL, outcome TEXT NOT NULL, note TEXT NOT NULL,
                    confirmed_at TEXT NOT NULL
                );
            """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        try:
            db.execute("PRAGMA journal_mode=WAL;")
            db.execute("PRAGMA busy_timeout=5000;")
        except sqlite3.Error:
            pass
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def transaction(self):
        with closing(self.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise


@dataclass(frozen=True)
class SendResult:
    status: str
    reminder_id: str
    message: str


class ReminderService:
    def __init__(self, store, settings: SendSettings, *, transport=None, clock=None):
        self.store = store
        self.settings = settings
        self.transport = transport or _smtp_submit
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def today(self):
        return self.clock().astimezone(ZoneInfo(self.settings.timezone)).date()

    @property
    def mode(self):
        return "dry_run" if self.settings.dry_run or self.settings.preview_only else "live"

    def import_invoices(self, records):
        valid = validate_records(records)
        with self.store.transaction() as db:
            for inv in valid:
                db.execute("""INSERT INTO invoices VALUES (?, ?, ?, ?)
                    ON CONFLICT(business_id, invoice_key) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at""",
                    (self.settings.business_id, inv["invoice_no"].casefold(), self._serialize(inv), self.clock().isoformat()))
        return valid

    @staticmethod
    def _serialize(invoice):
        return json.dumps(invoice, sort_keys=True, ensure_ascii=False)

    def _invoice(self, db, invoice_no):
        row = db.execute("SELECT data FROM invoices WHERE business_id=? AND invoice_key=?",
                         (self.settings.business_id, invoice_no.casefold())).fetchone()
        if row is None:
            raise ReminderError("This invoice has not been imported for this business.")
        return json.loads(row["data"])

    def _eligible(self, invoice):
        if not overdue_invoices([invoice], self.settings.days_threshold, today=self.today()):
            raise ReminderError("This invoice is paid, paused, or outside the overdue threshold.")

    def select_overdue(self, records):
        """Omit recent/uncertain live attempts before drafting; claim checks again."""
        eligible = overdue_invoices(records, self.settings.days_threshold, today=self.today())
        blocked = set()
        if self.mode == "live":
            cutoff = (self.clock() - timedelta(days=self.settings.cooldown_days)).isoformat()
            with closing(self.store.connect()) as db:
                blocked = {row["invoice_key"] for row in db.execute("""SELECT invoice_key FROM reminders
                    WHERE business_id=? AND mode='live' AND (state IN ('claimed','unknown')
                    OR (state='submitted' AND attempted_at>=?))""", (self.settings.business_id, cutoff))}
        selected = [inv for inv in eligible if inv["invoice_no"].casefold() not in blocked]
        return selected, len(eligible) - len(selected)

    def reconcile(self, approval_id, outcome, note):
        """Operator-confirmed recovery, never an automatic retry or model tool."""
        if outcome not in {"submitted", "failed"} or not isinstance(note, str) or not 10 <= len(note.strip()) <= 1000:
            raise ReminderError("Choose submitted or not sent, and record how you verified the outcome.")
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM reminders WHERE id=? AND business_id=?",
                             (approval_id, self.settings.business_id)).fetchone()
            if row is None or row["mode"] != "live" or row["state"] not in {"unknown", "claimed"}:
                raise ReminderError("This attempt is not awaiting reconciliation.")
            # Do not release a claim while a normal worker may still be submitting it.
            if row["state"] == "claimed" and self.clock() - datetime.fromisoformat(row["attempted_at"]) < timedelta(hours=1):
                raise ReminderError("This attempt may still be running. Wait one hour before reconciling an interrupted attempt.")
            db.execute("""INSERT INTO reconciliations
                (reminder_id,business_id,previous_state,outcome,note,confirmed_at) VALUES (?,?,?,?,?,?)""",
                (approval_id, self.settings.business_id, row["state"], outcome, note.strip(), self.clock().isoformat()))
            db.execute("UPDATE reminders SET state=?,completed_at=?,error=? WHERE id=? AND business_id=?",
                       (outcome, self.clock().isoformat(), "Operator verified: " + note.strip(), approval_id, self.settings.business_id))

    def approve(self, reviewed_invoice, subject, body):
        """Called only by UI/terminal approval; binds exact content, invoice and mode."""
        validate_message(subject, body)
        reviewed = validate_records([reviewed_invoice])[0]
        token = secrets.token_urlsafe(32)
        with self.store.transaction() as db:
            current = self._invoice(db, reviewed["invoice_no"])
            if current != reviewed:
                raise ReminderError("Invoice changed since review. Reload it and approve a fresh draft.")
            self._eligible(current)
            db.execute("""INSERT INTO reminders
                (id,business_id,invoice_key,invoice_data,sender,recipient,subject,body,mode,state,approved_at,message_id)
                VALUES (?,?,?,?,?,?,?,?,?,'approved',?,?)""",
                (token, self.settings.business_id, current["invoice_no"].casefold(), self._serialize(current),
                 self.settings.sender, current["email"], subject, body, self.mode, self.clock().isoformat(),
                 f"<{token}@payment-reminder.local>"))
        return token

    def _claim(self, approval_id):
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM reminders WHERE id=? AND business_id=?",
                             (approval_id, self.settings.business_id)).fetchone()
            if row is None or row["state"] != "approved":
                raise ReminderError("Approval is missing, already used, or belongs to another business.")
            if row["mode"] != self.mode or row["sender"] != self.settings.sender:
                raise ReminderError("Sending mode or sender changed. Review and approve again.")
            if self.clock() - datetime.fromisoformat(row["approved_at"]) > timedelta(minutes=30):
                raise ReminderError("Approval expired. Review and approve again.")
            current = self._invoice(db, row["invoice_key"])
            if self._serialize(current) != row["invoice_data"]:
                raise ReminderError("Invoice changed after approval. Reload and review again.")
            self._eligible(current)
            if self.mode == "live":
                email_address(self.settings.sender)
                if not self.settings.password:
                    raise ReminderError("Gmail App Password is required for live sending.")
                cutoff = (self.clock() - timedelta(days=self.settings.cooldown_days)).isoformat()
                prior = db.execute("""SELECT state FROM reminders WHERE business_id=? AND invoice_key=?
                    AND mode='live' AND (state IN ('claimed','unknown') OR (state='submitted' AND attempted_at>=?)) LIMIT 1""",
                    (self.settings.business_id, row["invoice_key"], cutoff)).fetchone()
                if prior:
                    if prior["state"] in {"claimed", "unknown"}:
                        raise ReminderError("A previous attempt is in progress or uncertain. Reconcile it before retrying.")
                    raise ReminderError(f"A reminder was already submitted within {self.settings.cooldown_days} days.")
            db.execute("UPDATE reminders SET state='claimed',attempted_at=? WHERE id=?",
                       (self.clock().isoformat(), approval_id))
            return dict(row)

    def _finish(self, approval_id, state, error=""):
        with self.store.transaction() as db:
            db.execute("UPDATE reminders SET state=?,completed_at=?,error=? WHERE id=? AND business_id=?",
                       (state, self.clock().isoformat(), error, approval_id, self.settings.business_id))

    def send_approved(self, approval_id):
        try:
            row = self._claim(approval_id)
        except (ReminderError, ValueError) as exc:
            return SendResult("blocked", approval_id, str(exc))
        except sqlite3.Error:
            return SendResult("blocked", approval_id, "History could not be saved. Nothing was sent.")
        state, detail = "previewed", "Preview recorded; no email was sent."
        if self.mode == "live":
            try:
                self.transport(self.settings, row["recipient"], row["subject"], row["body"], row["message_id"])
                state, detail = "submitted", "Accepted by Gmail. Delivery and payment are not yet confirmed."
            except DeliveryFailure as exc:
                state, detail = "failed", str(exc)
            except Exception:
                state, detail = "unknown", "Submission outcome is uncertain. Check Sent mail before retrying."
        try:
            self._finish(approval_id, state, detail if state in {"failed", "unknown"} else "")
        except sqlite3.Error:
            # The durable claim remains, blocking retries even when final logging fails.
            return SendResult("unknown", approval_id, "Final history could not be saved. Do not resend until this attempt is reconciled.")
        return SendResult(state, approval_id, detail)

    def history(self):
        with closing(self.store.connect()) as db:
            return [dict(row) for row in db.execute("SELECT * FROM reminders WHERE business_id=? ORDER BY approved_at DESC",
                                                    (self.settings.business_id,))]

    def history_csv(self):
        output = io.StringIO(newline="")
        fields = ["reminder_id", "invoice_no", "client_name", "recipient", "currency", "outstanding_amount",
                  "mode", "status", "approved_at", "attempted_at", "completed_at", "subject", "body", "error"]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for row in self.history():
            invoice = json.loads(row["invoice_data"])
            result = {k: row.get(k, "") for k in fields}
            result.update(reminder_id=row["id"], status=row["state"], **{k: invoice[k] for k in ("invoice_no", "client_name", "currency", "outstanding_amount")})
            # Prevent formula execution when an exported CSV is opened in Excel.
            writer.writerow({k: ("'"+str(v) if str(v).lstrip().startswith(("=", "+", "-", "@")) else v) for k,v in result.items()})
        return output.getvalue().encode("utf-8-sig")
