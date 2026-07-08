"""
tools.py
Concrete implementations for every tool Claude / Groq can call.
No AI here — just plain Python doing the actual work.

Key change from the paid version:
  send_whatsapp (Wati API) → send_email (Gmail SMTP, free)
"""

import csv
import json
import smtplib
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pandas as pd

from config import GMAIL_USER, GMAIL_APP_PASSWORD, LOG_PATH, DRY_RUN

# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _ok(payload: dict) -> str:
    return json.dumps({"status": "ok", **payload})

def _err(message: str) -> str:
    return json.dumps({"status": "error", "message": message})


# ─────────────────────────────────────────────────────────────
# Tool 1 — read_invoices
# ─────────────────────────────────────────────────────────────

def read_invoices(xlsx_path: str) -> str:
    """
    Load all rows from the Excel file.
    Normalises column names to snake_case.
    Converts date columns to ISO-8601 strings so they're JSON-safe.
    """
    path = Path(xlsx_path)
    if not path.exists():
        return _err(f"File not found: {xlsx_path}")

    try:
        df = pd.read_excel(path, dtype=str)
        df.columns = [
            c.strip().lower().replace(" ", "_").replace("-", "_")
            for c in df.columns
        ]
        df = df.fillna("")

        # Parse and reformat any date columns
        for col in [c for c in df.columns if "date" in c]:
            df[col] = (
                pd.to_datetime(df[col], errors="coerce")
                .dt.strftime("%Y-%m-%d")
                .fillna("")
            )

        return _ok({"count": len(df), "invoices": df.to_dict(orient="records")})

    except Exception as exc:
        return _err(str(exc))


# ─────────────────────────────────────────────────────────────
# Tool 2 — filter_overdue
# ─────────────────────────────────────────────────────────────

def filter_overdue(invoices_json: str, days_threshold: int = 7) -> str:
    """
    Keep only unpaid invoices whose due_date is at least
    `days_threshold` days ago. Adds `days_overdue` to each record.
    Returns sorted by most overdue first.
    """
    try:
        data       = json.loads(invoices_json)
        invoices   = data.get("invoices", []) if isinstance(data, dict) else data
        today      = date.today()
        overdue: list[dict] = []

        for inv in invoices:
            # Skip paid / cancelled rows
            if str(inv.get("status", "")).strip().lower() in ("paid", "cancelled", "cleared"):
                continue

            raw_due = inv.get("due_date") or inv.get("invoice_date") or ""
            if not raw_due:
                continue

            try:
                due       = date.fromisoformat(str(raw_due)[:10])
                days_late = (today - due).days
            except ValueError:
                continue

            if days_late >= days_threshold:
                record = dict(inv)
                record["days_overdue"] = days_late
                overdue.append(record)

        overdue.sort(key=lambda r: r["days_overdue"], reverse=True)

        return _ok({"overdue_count": len(overdue), "overdue_invoices": overdue})

    except Exception as exc:
        return _err(str(exc))


# ─────────────────────────────────────────────────────────────
# Tool 3 — preview_and_approve
# ─────────────────────────────────────────────────────────────

def preview_and_approve(
    client_name: str,
    email: str,
    invoice_no: str,
    amount: str,
    days_overdue: int,
    subject: str,
    body: str,
) -> str:
    """
    Print the drafted email to the terminal and ask the operator
    to approve (y), skip (n), or edit (e) before sending.
    Blocks until the operator responds.
    """
    div = "─" * 60

    print(f"\n{div}")
    print(f"  Client   : {client_name}")
    print(f"  Email    : {email}")
    print(f"  Invoice  : {invoice_no}  |  ₹{amount}  |  {days_overdue}d overdue")
    print(div)
    print(f"  Subject  : {subject}")
    print(div)
    print("  Body:\n")
    for line in body.strip().split("\n"):
        print(f"    {line}")
    print(f"\n{div}")

    while True:
        choice = input("  Send? [y = yes / n = skip / e = edit]: ").strip().lower()

        if choice == "y":
            return _ok({"approved": True, "subject": subject, "body": body})

        elif choice == "n":
            print("  Skipped.\n")
            return _ok({"approved": False, "reason": "Operator skipped"})

        elif choice == "e":
            print("  Editing subject (press ENTER to keep current):")
            new_subject = input(f"  [{subject}] > ").strip()
            if not new_subject:
                new_subject = subject

            print("  Paste edited body (use \\n for line breaks, ENTER when done):")
            new_body = input("  > ").replace("\\n", "\n").strip()
            if not new_body:
                new_body = body

            confirm = input("  Send edited version? [y/n]: ").strip().lower()
            if confirm == "y":
                return _ok({"approved": True, "subject": new_subject, "body": new_body})

            print("  Cancelled — skipping this invoice.\n")
            return _ok({"approved": False, "reason": "Operator cancelled after editing"})

        else:
            print("  Please enter y, n, or e.")


# ─────────────────────────────────────────────────────────────
# Tool 4 — send_email  (replaces send_whatsapp from paid version)
# ─────────────────────────────────────────────────────────────

def send_email(to_email: str, subject: str, body: str) -> str:
    """
    Send a payment reminder email via Gmail SMTP.

    DRY_RUN=true  → prints to terminal only, does NOT send.
    DRY_RUN=false → sends via Gmail (needs GMAIL_USER + GMAIL_APP_PASSWORD).

    Getting an App Password:
      Google Account → Security → 2-Step Verification (enable) →
      App Passwords → select app "Mail" → generate → copy 16-char password
    """
    if DRY_RUN:
        print(f"\n  [DRY RUN] Would email → {to_email}")
        print(f"  Subject: {subject}")
        print(f"  {body[:100]}{'...' if len(body) > 100 else ''}\n")
        return _ok({"mode": "dry_run", "to": to_email})

    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        return _err("GMAIL_USER or GMAIL_APP_PASSWORD not set in .env")

    try:
        msg = MIMEMultipart()
        msg["From"]    = GMAIL_USER
        msg["To"]      = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        # Try SSL on port 465 first; fall back to STARTTLS on port 587
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as smtp:
                smtp.login(GMAIL_USER, GMAIL_APP_PASSWORD)
                smtp.send_message(msg)
        except smtplib.SMTPException:
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.login(GMAIL_USER, GMAIL_APP_PASSWORD)
                smtp.send_message(msg)

        return _ok({"sent_to": to_email, "subject": subject})

    except Exception as exc:
        return _err(str(exc))


# ─────────────────────────────────────────────────────────────
# Tool 5 — log_reminder_sent
# ─────────────────────────────────────────────────────────────

_LOG_FIELDS = [
    "timestamp", "invoice_no", "client_name",
    "email", "amount", "days_overdue", "body_preview",
]


def log_reminder_sent(
    invoice_no: str,
    client_name: str,
    email: str,
    amount: str,
    days_overdue: int,
    body: str,
) -> str:
    """
    Append a row to reminders_log.csv.
    Creates the file with a header row on first run.
    """
    try:
        log_path   = Path(LOG_PATH)
        new_file   = not log_path.exists() or log_path.stat().st_size == 0

        with open(log_path, "a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=_LOG_FIELDS)
            if new_file:
                writer.writeheader()
            writer.writerow({
                "timestamp":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "invoice_no":   invoice_no,
                "client_name":  client_name,
                "email":        email,
                "amount":       amount,
                "days_overdue": days_overdue,
                "body_preview": body[:120],
            })

        return _ok({"logged": invoice_no, "log_file": str(log_path)})

    except Exception as exc:
        return _err(str(exc))
