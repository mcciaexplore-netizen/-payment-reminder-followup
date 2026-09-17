"""Read-only compatibility tools and terminal review. Sending lives in reminders.py."""
import json
from invoices import InvoiceValidationError, overdue_invoices, read_invoice_file


def read_invoices(xlsx_path: str) -> str:
    try:
        rows = read_invoice_file(xlsx_path)
        return json.dumps({"status": "ok", "count": len(rows), "invoices": rows})
    except (InvoiceValidationError, OSError) as exc:
        return json.dumps({"status": "error", "message": str(exc), "errors": getattr(exc, "errors", [str(exc)])})


def filter_overdue(invoices_json: str, days_threshold: int = 7) -> str:
    try:
        data = json.loads(invoices_json)
        if isinstance(data, dict) and data.get("status") == "error":
            return json.dumps(data)
        rows = data.get("invoices", []) if isinstance(data, dict) else data
        selected = overdue_invoices(rows, days_threshold)
        return json.dumps({"status": "ok", "overdue_count": len(selected), "overdue_invoices": selected})
    except (ValueError, TypeError) as exc:
        return json.dumps({"status": "error", "message": str(exc)})


def preview_and_approve(client_name, email, invoice_no, amount, days_overdue, subject, body):
    print(f"\n{client_name} <{email}> | {invoice_no} | Outstanding: {amount} | {days_overdue} days overdue")
    print(f"Subject: {subject}\n\n{body}\n")
    while True:
        choice = input("Approve this message? [y=yes / n=skip / e=edit]: ").strip().lower()
        if choice == "n":
            return json.dumps({"status": "ok", "approved": False})
        if choice == "y":
            return json.dumps({"status": "ok", "approved": True, "subject": subject, "body": body})
        if choice == "e":
            subject = input("New subject (ENTER to keep): ").strip() or subject
            body = input("New body (use \\n for line breaks; ENTER to keep): ").replace("\\n", "\n").strip() or body
            print(f"Subject: {subject}\n\n{body}\n")
