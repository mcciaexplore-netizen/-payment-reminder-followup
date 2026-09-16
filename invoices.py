"""Strict invoice imports and balance/date rules shared by UI and CLI."""
import io
import re
import zipfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import pandas as pd

REQUIRED = {"invoice_no", "client_name", "email", "amount", "due_date", "status"}
MAX_ROWS = 5000
MAX_BYTES = 5 * 1024 * 1024
OPEN_STATUSES = {"unpaid", "partially_paid"}
CLOSED_STATUSES = {"paid", "cancelled", "cleared", "disputed", "on_hold"}
ALIASES = {"canceled": "cancelled", "partial": "partially_paid", "partially paid": "partially_paid", "on hold": "on_hold"}


class InvoiceValidationError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("Please correct the invoice file: " + "; ".join(errors[:5]))


def email_address(value: str) -> str:
    value = str(value).strip()
    if len(value) > 254 or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}", value):
        raise ValueError("Email must contain one valid address, without display names or extra recipients.")
    if ".." in value or value.startswith(".") or ".@" in value:
        raise ValueError("Email contains an invalid dot sequence.")
    return value


def money(value, label="Amount") -> Decimal:
    text = str(value).strip()
    if not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})*,\d{3})(?:\.\d{1,2})?", text):
        raise ValueError(f"{label} must be a non-negative number with at most two decimal places.")
    try:
        result = Decimal(text.replace(",", ""))
        if not result.is_finite() or result > Decimal("999999999999.99"):
            raise InvalidOperation
        return result.quantize(Decimal("0.01"))
    except InvalidOperation:
        raise ValueError(f"{label} is outside the supported range.") from None


def invoice_date(value) -> str:
    if isinstance(value, (date, datetime)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError("Due Date is required; use YYYY-MM-DD, DD/MM/YYYY or an Excel date.")


def validate_records(records: list[dict]) -> list[dict]:
    if not isinstance(records, list) or not records:
        raise InvoiceValidationError(["The file must contain at least one invoice."])
    if len(records) > MAX_ROWS:
        raise InvoiceValidationError([f"Import at most {MAX_ROWS} rows at a time."])
    valid, errors, seen = [], [], set()
    for row_number, row in enumerate(records, 2):
        try:
            if not isinstance(row, dict) or REQUIRED - row.keys():
                raise ValueError("Missing required invoice fields.")
            inv = {str(k): ("" if v is None else str(v).strip()) for k, v in row.items()}
            number = inv["invoice_no"]
            if not number or len(number) > 100:
                raise ValueError("Invoice No is required and must be at most 100 characters.")
            identity = number.casefold()
            if identity in seen:
                raise ValueError(f"Duplicate invoice number: {number}.")
            seen.add(identity)
            if not inv["client_name"] or len(inv["client_name"]) > 200:
                raise ValueError("Client Name is required and must be at most 200 characters.")
            status = inv["status"].lower()
            status = ALIASES.get(status, status)
            if status not in OPEN_STATUSES | CLOSED_STATUSES:
                raise ValueError("Status must be Unpaid, Partially Paid, Paid, Cleared, Cancelled, Disputed or On Hold.")
            total = money(inv["amount"])
            paid_text = inv.get("amount_paid", "")
            notes = inv.get("notes", "")
            if not paid_text and (status == "partially_paid" or re.search(r"\bpartial(?:ly)?\b", notes, re.I)):
                raise ValueError("Enter partial receipts in the Amount Paid column; notes are not used for balances.")
            paid = money(paid_text, "Amount Paid") if paid_text else (total if status in {"paid", "cleared"} else Decimal("0.00"))
            if paid > total:
                raise ValueError("Amount Paid cannot exceed Amount.")
            if status == "partially_paid" and not Decimal("0") < paid < total:
                raise ValueError("Partially Paid requires Amount Paid greater than zero and less than Amount.")
            if status in {"paid", "cleared"} and paid != total:
                raise ValueError("A paid/cleared invoice must have no outstanding balance.")
            address = email_address(inv["email"]) if inv["email"] or status in OPEN_STATUSES else ""
            inv.update(invoice_no=number, email=address, status=status,
                       amount=str(total), amount_paid=str(paid), outstanding_amount=str(total-paid),
                       due_date=invoice_date(row["due_date"]), currency=(inv.get("currency") or "INR").upper())
            if not re.fullmatch(r"[A-Z]{3}", inv["currency"]):
                raise ValueError("Currency must be a three-letter code such as INR.")
            fields = ("invoice_no", "client_name", "email", "amount", "amount_paid", "outstanding_amount", "due_date", "status", "currency")
            valid.append({k: inv[k] for k in fields} | {"notes": notes[:2000]})
        except (ValueError, TypeError) as exc:
            errors.append(f"Row {row_number}: {exc}")
    if errors:
        raise InvoiceValidationError(errors)
    return valid


def read_invoice_file(source, filename: str | None = None, *, column_mapping=None, headers_only=False, default_currency="INR") -> list[dict]:
    if isinstance(source, (str, Path)):
        path = Path(source)
        if path.stat().st_size > MAX_BYTES:
            raise InvoiceValidationError(["File must be 5 MB or smaller."])
        name, content = path.name, path.read_bytes()
    else:
        name = filename or getattr(source, "name", "invoices.xlsx")
        content = source.getvalue() if hasattr(source, "getvalue") else source.read(MAX_BYTES+1)
    if len(content) > MAX_BYTES:
        raise InvoiceValidationError(["File must be 5 MB or smaller."])
    try:
        if str(name).lower().endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content), header=None, dtype=str, keep_default_na=False, nrows=MAX_ROWS+2)
        elif str(name).lower().endswith(".xlsx"):
            with zipfile.ZipFile(io.BytesIO(content)) as workbook:
                if sum(part.file_size for part in workbook.infolist()) > 50 * 1024 * 1024:
                    raise ValueError("Workbook expands beyond the 50 MB processing limit.")
            df = pd.read_excel(io.BytesIO(content), header=None, dtype=object, keep_default_na=False, nrows=MAX_ROWS+2)
        else:
            raise ValueError("Only .xlsx and .csv files are supported.")
        if df.empty:
            raise ValueError("The file is empty.")
        # Read headers as data so pandas cannot silently rename duplicate headers.
        columns = [re.sub(r"[\s-]+", "_", str(c).strip().lower()) for c in df.iloc[0]]
        if len(columns) != len(set(columns)):
            raise ValueError("Two column names map to the same field.")
        if headers_only:
            return columns
        if column_mapping is not None:
            if not isinstance(column_mapping, dict) or not set(column_mapping)<=set(columns):
                raise ValueError("Column mapping contains unknown source columns.")
            if len(set(column_mapping.values()))!=len(column_mapping):
                raise ValueError("Map each destination field only once.")
            indices=[i for i,c in enumerate(columns) if c in column_mapping]
            df=df.iloc[:,indices]
            columns=[column_mapping[columns[i]] for i in indices]
        missing = REQUIRED - set(columns)
        if missing:
            raise ValueError("Missing columns: " + ", ".join(sorted(missing)))
        df = df.iloc[1:].copy()
        df.columns = columns
        records=df.to_dict(orient="records")
        for row in records:
            row["currency"]=row.get("currency") or default_currency
        return validate_records(records)
    except InvoiceValidationError:
        raise
    except Exception as exc:
        raise InvoiceValidationError([f"Could not read file: {exc}"]) from exc


def overdue_invoices(records, days_threshold=7, *, today=None):
    if isinstance(days_threshold, bool) or not isinstance(days_threshold, int) or not 1 <= days_threshold <= 365:
        raise ValueError("Overdue threshold must be between 1 and 365 days.")
    today = today or date.today()
    result = []
    for inv in validate_records(records):
        late = (today - date.fromisoformat(inv["due_date"])).days
        if inv["status"] in OPEN_STATUSES and Decimal(inv["outstanding_amount"]) > 0 and late >= days_threshold:
            result.append({**inv, "days_overdue": late})
    return sorted(result, key=lambda r: (-r["days_overdue"], r["invoice_no"]))
