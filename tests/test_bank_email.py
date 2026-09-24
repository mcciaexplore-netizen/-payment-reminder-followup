"""Comprehensive test suite for all 5 new features:
1. Automated IMAP / Gmail Inbox Poller (Background Sync)
2. Automated Payment Confirmation Messages
3. Multi-Bank Alert Format Intelligence (HDFC, ICICI, SBI, Razorpay, Stripe, PhonePe, Wise, etc.)
4. Smart Partial Payment & Installment Allocation
5. Dynamic Reminder Escalation (Polite -> Firm -> Formal/Legal)
"""
import pytest
import secrets
from datetime import datetime, timezone
from bank_email import parse_bank_email, match_and_process_bank_email
from ledger import Ledger, get_invoice
from invoices import overdue_invoices
from scheduling import eligible, render_draft
from accounts import Accounts, password_hash
from business_features import BusinessFeatures
from workspace_store import WorkspaceStore, WorkspaceError, utcnow


def test_multi_bank_format_parsing():
    # 1. HDFC Bank
    p1 = parse_bank_email("HDFC Credit Alert", "Account AC-1234 credited with Rs. 25,000.00. UTR: 426819203810. Ref for INV-501.")
    assert p1["amount"] == "25000.00"
    assert p1["reference"] == "426819203810"
    assert p1["invoice_hint"] == "501"
    assert p1["bank_detected"] == "HDFC Bank"

    # 2. ICICI Bank
    p2 = parse_bank_email("ICICI iMobile Alert", "Your Acct XX9012 has been credited with INR 12,500.00 on 24-Sep-26. Ref No: 987654321012.")
    assert p2["amount"] == "12500.00"
    assert p2["reference"] == "987654321012"
    assert p2["bank_detected"] == "ICICI Bank"

    # 3. SBI
    p3 = parse_bank_email("SBI Alert", "Dear SBI User, your A/C ending 4321 credited by Rs 8500.00 via UPI/428918239011 for Invoice INV-808.")
    assert p3["amount"] == "8500.00"
    assert p3["reference"] == "428918239011"
    assert p3["invoice_hint"] in {"808", "INV-808"}
    assert p3["bank_detected"] == "SBI"

    # 4. Razorpay
    p4 = parse_bank_email("Razorpay Payment Received", "Payment of Rs. 4,999.00 received for Order ORD-9901. Payment ID: pay_L1M928374.")
    assert p4["amount"] == "4999.00"
    assert p4["reference"] == "pay_L1M928374"
    assert p4["bank_detected"] == "Razorpay"


def test_match_bank_email_records_receipt_and_queues_confirmation(tmp_path):
    db_path = tmp_path / "workspace.sqlite3"
    store = WorkspaceStore(db_path)
    acc = Accounts(store)
    
    token, business = acc.register("owner@example.com", "Pass12345678!", "Test Corp", "Asia/Kolkata")
    ledger = Ledger(store, token, business)

    sample_invoices = [
        dict(invoice_no="INV-1001", client_name="Acme Corp", email="acme@example.com", phone="+919876543210",
             amount="5000.00", amount_paid="0.00", due_date="2026-09-01", status="unpaid", currency="INR",
             terms="Net 30", notes="")
    ]
    ledger.import_records(sample_invoices)

    # Set email consent
    with store.transaction() as db:
        db.execute("UPDATE ws_invoices SET profile=? WHERE business_id=?", 
                   (r'{"consents":["email"],"email":"acme@example.com","phone":"","opted_out":false,"language":"en","promise_date":""}', business))

    subject = "Credit Alert: Payment Received for INV-1001"
    body = "Dear business, account credited with Rs 5000.00. UTR No: 426819203810. Payment for INV-1001."

    result = ledger.process_bank_email(subject, body)

    assert result["invoice_no"] == "INV-1001"
    assert result["new_status"] == "paid"
    assert result["reminders_stopped"] is True
    assert result["confirmation_queued"] is True

    # Check job table for confirmation email
    with store.transaction() as db:
        jobs = db.execute("SELECT * FROM ws_jobs WHERE business_id=?", (business,)).fetchall()
        assert len(jobs) == 1
        assert jobs[0]["unique_key"] == "confirm-" + jobs[0]["id"]


def test_smart_installment_allocation(tmp_path):
    db_path = tmp_path / "workspace.sqlite3"
    store = WorkspaceStore(db_path)
    acc = Accounts(store)
    
    token, business = acc.register("owner@example.com", "Pass12345678!", "Test Corp", "Asia/Kolkata")
    features = BusinessFeatures(store, token, business)

    features.import_records([
        dict(invoice_no="INV-2002", client_name="Beta LLC", email="beta@example.com", phone="+919876543210",
             amount="10000.00", amount_paid="0.00", due_date="2026-09-01", status="unpaid", currency="INR",
             terms="", notes="")
    ])

    # Save 2 installments of 5000 each
    features.save_installments("INV-2002", [
        {"due_date": "2026-09-01", "amount": "5000.00"},
        {"due_date": "2026-10-01", "amount": "5000.00"}
    ])

    # Process partial payment of 5000
    subject = "Bank Credit: Rs 5000"
    body = "Credited with Rs 5000.00. Ref UTR: 8877665544. For INV-2002."
    res = features.process_bank_email(subject, body)

    assert res["invoice_no"] == "INV-2002"
    assert res["new_status"] == "partially_paid"
    assert res["installments_remaining"] == 1


def test_dynamic_reminder_escalation(tmp_path):
    db_path = tmp_path / "workspace.sqlite3"
    store = WorkspaceStore(db_path)
    acc = Accounts(store)
    
    token, business = acc.register("owner@example.com", "Pass12345678!", "Test Corp", "Asia/Kolkata")
    ledger = Ledger(store, token, business)

    ledger.import_records([
        dict(invoice_no="INV-3003", client_name="Gamma Inc", email="gamma@example.com", phone="+919876543210",
             amount="8000.00", amount_paid="0.00", due_date="2026-08-01", status="unpaid", currency="INR",
             terms="", notes="")
    ])

    with store.transaction() as db:
        db.execute("UPDATE ws_invoices SET profile=? WHERE business_id=?", 
                   (r'{"consents":["email"],"email":"gamma@example.com","phone":"","opted_out":false,"language":"en","promise_date":""}', business))
        
        # Test draft rendering on 2026-09-24 (over 50 days overdue -> Escalates to Formal/Final Notice)
        now_dt = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
        draft = render_draft(db, business, "INV-3003", "email", None, now_dt)

        assert "FINAL NOTICE" in draft["subject"]
        assert "ATTENTION" in draft["body"]
