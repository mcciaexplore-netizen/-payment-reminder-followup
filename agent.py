#!/usr/bin/env python3
"""Deterministic CLI workflow. AI can suggest wording, never invoke sending tools."""
import argparse
import json
import sqlite3
import sys
from collections import Counter

import config
from config import SendSettings
from drafting import draft_email
from invoices import InvoiceValidationError, read_invoice_file
from reminders import ReminderError, ReminderService, ReminderStore
from tools import filter_overdue, preview_and_approve, read_invoices


def run_tool(name: str, inputs: dict) -> str:
    """Legacy read-only dispatcher. Side effects are deliberately unavailable."""
    try:
        if name == "read_invoices":
            return read_invoices(inputs["xlsx_path"])
        if name == "filter_overdue":
            return filter_overdue(inputs["invoices_json"], int(inputs.get("days_threshold", config.DEFAULT_OVERDUE_DAYS)))
        return json.dumps({"status": "error", "message": "This tool is unavailable. AI tools cannot approve, send or log reminders."})
    except (KeyError, TypeError, ValueError) as exc:
        return json.dumps({"status": "error", "message": str(exc)})


def run_agent(xlsx_path, days_threshold, *, dry_run=None, use_ai=False):
    settings = SendSettings(days_threshold=days_threshold, dry_run=config.DRY_RUN if dry_run is None else dry_run)
    service = ReminderService(ReminderStore(config.DATABASE_PATH), settings)
    print(f"Payment follow-up | {config.BUSINESS_NAME}")
    print("Mode: PREVIEW ONLY - no emails will be sent" if service.mode == "dry_run" else "Mode: LIVE - approved emails will be sent")
    valid = service.import_invoices(read_invoice_file(xlsx_path))
    overdue, suppressed = service.select_overdue(valid)
    if suppressed:
        print(f"{suppressed} invoices excluded because of recent or unresolved reminders.")
    if not overdue:
        print("No eligible overdue invoices found in this file.")
        return 0
    counts = Counter()
    for invoice in overdue:
        draft = draft_email(invoice, config.BUSINESS_NAME, config.GROQ_API_KEY if use_ai else "", config.GROQ_MODEL)
        review = json.loads(preview_and_approve(invoice["client_name"], invoice["email"], invoice["invoice_no"],
                            f"{invoice['currency']} {invoice['outstanding_amount']}", invoice["days_overdue"], draft.subject, draft.body))
        if not review["approved"]:
            counts["skipped"] += 1
            continue
        try:
            approval = service.approve(invoice, review["subject"], review["body"])
            result = service.send_approved(approval)
            counts[result.status] += 1
            print(f"{invoice['invoice_no']}: {result.status} - {result.message}")
        except (ReminderError, ValueError, sqlite3.Error) as exc:
            counts["blocked"] += 1
            print(f"{invoice['invoice_no']}: blocked - {exc}")
    print("Summary: " + ", ".join(f"{name}={count}" for name, count in sorted(counts.items())))
    return 1 if any(counts[s] for s in ("blocked", "unknown", "failed")) else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Review and send payment reminders.")
    parser.add_argument("--file", "-f", default=str(config.INVOICES_PATH))
    parser.add_argument("--days", "-d", type=int, default=config.DEFAULT_OVERDUE_DAYS)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--no-dry-run", dest="dry_run", action="store_false", help="Enable live sending after human approval.")
    mode.add_argument("--dry-run", dest="dry_run", action="store_true", help="Preview only; no email is sent.")
    parser.set_defaults(dry_run=config.DRY_RUN)
    parser.add_argument("--ai", action="store_true", help="Use optional AI wording; invoice data is not sent to the model.")
    args = parser.parse_args(argv)
    try:
        return run_agent(args.file, args.days, dry_run=args.dry_run, use_ai=args.ai)
    except InvoiceValidationError as exc:
        for error in exc.errors:
            print(error, file=sys.stderr)
        return 2
    except (ValueError, OSError, sqlite3.Error, EOFError) as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
