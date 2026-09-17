import io
from decimal import Decimal
import pandas as pd
import pytest
from invoices import InvoiceValidationError, read_invoice_file, validate_records, overdue_invoices, money
from setup_sample import sample_bytes


def test_sample_correct_outstanding_balances():
    rows = read_invoice_file(io.BytesIO(sample_bytes()))
    overdue = overdue_invoices(rows)
    assert len(overdue) == 4
    assert sum(Decimal(r["outstanding_amount"]) for r in overdue) == Decimal("153000.00")
    assert next(r for r in overdue if r["invoice_no"] == "INV-002")["outstanding_amount"] == "10500.00"


@pytest.mark.parametrize("change", [
    {"due_date": ""}, {"due_date": "2026-02-30"}, {"email": "bad email"},
    {"email": "one@example.com,two@example.com"}, {"email": "one@example.com\r\nBcc: two@example.com"},
    {"client_name": ""}, {"status": ""}, {"status": "unknown"}, {"invoice_no": ""},
    {"amount": "-1"}, {"amount": "NaN"}, {"amount": "Infinity"}, {"amount": "12.345"},
    {"amount": "1,2,3"}, {"amount_paid": "13000"}, {"status": "Paid", "amount_paid": "1"},
    {"status": "Partially Paid", "amount_paid": ""},
    {"status": "Partially Paid", "amount_paid": "0"},
    {"status": "Partially Paid", "amount_paid": "12500"},
])
def test_invalid_rows_stop_import(invoice, change):
    with pytest.raises(InvoiceValidationError, match="Row 2"):
        validate_records([{**invoice, **change}])


def test_no_invoice_date_fallback(invoice):
    with pytest.raises(InvoiceValidationError, match="Due Date"):
        validate_records([{**invoice, "due_date": "", "invoice_date": "2025-01-01"}])


def test_duplicate_ids_case_insensitive(invoice):
    with pytest.raises(InvoiceValidationError, match="Duplicate"):
        validate_records([invoice, {**invoice, "invoice_no": "inv-001"}])


def test_missing_columns_are_an_error():
    with pytest.raises(InvoiceValidationError, match="Missing columns"):
        read_invoice_file(io.BytesIO(b"Random\n1\n"), "test.csv")


def test_duplicate_source_headers_are_not_silently_renamed():
    content = b"Invoice No,Client Name,Email,Amount,Amount,Due Date,Status\nINV1,Test,test@example.com,100,200,2026-01-01,Unpaid\n"
    with pytest.raises(InvoiceValidationError, match="same field"):
        read_invoice_file(io.BytesIO(content), "test.csv")


def test_invalid_excel_is_an_error():
    with pytest.raises(InvoiceValidationError):
        read_invoice_file(io.BytesIO(b"not excel"))


@pytest.mark.parametrize("status", ["Paid", "Cleared", "Cancelled", "Canceled", "Disputed", "On Hold"])
def test_closed_and_paused_not_reminded(invoice, status):
    changed = {**invoice, "status": status, "amount_paid": "12500" if status in {"Paid", "Cleared"} else "2000"}
    assert overdue_invoices([changed]) == []


def test_exact_money_and_explicit_day_first_date(invoice):
    assert money("1,45,000.55") == Decimal("145000.55")
    assert validate_records([{**invoice, "due_date": "01/08/2026"}])[0]["due_date"] == "2026-08-01"


def test_native_excel_dates_and_leading_zero_identifiers(invoice):
    frame = pd.DataFrame([{**invoice, "invoice_no": "0000123", "due_date": pd.Timestamp("2026-08-01")}])
    buf = io.BytesIO()
    frame.to_excel(buf, index=False)
    assert read_invoice_file(buf)[0]["invoice_no"] == "0000123"


@pytest.mark.parametrize("threshold", [0, -1, True, "7", 366])
def test_threshold_validated_server_side(invoice, threshold):
    with pytest.raises(ValueError):
        overdue_invoices([invoice], threshold)
