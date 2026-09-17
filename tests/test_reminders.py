import csv
import io
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock
import pytest
from reminders import ReminderError, ReminderService, DeliveryFailure


def approve(service, invoice, subject="Reviewed subject", body="Reviewed body"):
    valid = service.import_invoices([invoice])[0]
    return service.approve(valid, subject, body)


def test_cannot_send_without_approval(service):
    service.transport = Mock()
    assert service.send_approved("invented-token").status == "blocked"
    service.transport.assert_not_called()


def test_sends_exact_reviewed_content_and_token_only_once(service, invoice):
    service.transport = Mock()
    token = approve(service, invoice)
    assert service.send_approved(token).status == "submitted"
    call = service.transport.call_args.args
    assert call[1:4] == (invoice["email"], "Reviewed subject", "Reviewed body")
    assert service.send_approved(token).status == "blocked"
    assert service.transport.call_count == 1
    assert "test-secret" not in service.history_csv().decode("utf-8-sig")
    assert "test-secret" not in repr(service.settings)


def test_changed_balance_or_recipient_invalidates_approval(service, invoice):
    token = approve(service, invoice)
    service.import_invoices([{**invoice, "email": "different@example.com", "amount_paid": "4000"}])
    service.transport = Mock()
    assert service.send_approved(token).status == "blocked"
    service.transport.assert_not_called()


def test_stale_review_cannot_approve_new_invoice_data(service, invoice):
    old = service.import_invoices([invoice])[0]
    service.import_invoices([{**invoice, "amount_paid": "4000"}])
    with pytest.raises(ReminderError, match="changed"):
        service.approve(old, "Subject", "Body")


def test_paid_after_approval_blocks_send(service, invoice):
    token = approve(service, invoice)
    service.import_invoices([{**invoice, "status": "Paid", "amount_paid": "12500"}])
    assert service.send_approved(token).status == "blocked"


def test_invalid_import_does_not_update_any_rows(service, invoice):
    original = service.import_invoices([invoice])[0]
    with pytest.raises(ValueError):
        service.import_invoices([{**invoice, "amount_paid": "3000"}, {**invoice, "invoice_no": "BAD", "due_date": ""}])
    assert service.approve(original, "Subject", "Body")


def test_cooldown_survives_new_service_and_different_draft(service, invoice):
    assert service.send_approved(approve(service, invoice)).status == "submitted"
    reloaded = ReminderService(service.store, service.settings, transport=Mock(), clock=service.clock)
    token = approve(reloaded, invoice, "New subject", "New body")
    assert reloaded.send_approved(token).status == "blocked"
    reloaded.transport.assert_not_called()


def test_simultaneous_sends_have_one_transport_call(service, invoice):
    service.transport = Mock()
    tokens = [approve(service, invoice), approve(service, invoice)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = [r.status for r in pool.map(service.send_approved, tokens)]
    assert sorted(statuses) == ["blocked", "submitted"]
    assert service.transport.call_count == 1


def test_preview_has_distinct_history_and_does_not_block_live(service, invoice):
    preview = ReminderService(service.store, replace(service.settings, dry_run=True), transport=Mock(), clock=service.clock)
    token = approve(preview, invoice)
    assert preview.send_approved(token).status == "previewed"
    preview.transport.assert_not_called()
    assert service.send_approved(approve(service, invoice)).status == "submitted"
    history = service.history()
    assert {(r["mode"], r["state"]) for r in history} == {("dry_run", "previewed"), ("live", "submitted")}


def test_hosted_demo_cannot_send_even_when_dry_run_is_false(service, invoice):
    demo = ReminderService(service.store, replace(service.settings, preview_only=True, dry_run=False), transport=Mock(), clock=service.clock)
    assert demo.send_approved(approve(demo, invoice)).status == "previewed"
    demo.transport.assert_not_called()


def test_cannot_reuse_preview_approval_for_live(service, invoice):
    preview = ReminderService(service.store, replace(service.settings, dry_run=True), clock=service.clock)
    token = approve(preview, invoice)
    assert service.send_approved(token).status == "blocked"


def test_sender_change_invalidates_approval(service, invoice):
    token = approve(service, invoice)
    changed = ReminderService(service.store, replace(service.settings, sender="other@example.com"), clock=service.clock)
    assert changed.send_approved(token).status == "blocked"


def test_business_isolation_for_approval_and_export(service, invoice):
    token = approve(service, invoice)
    other = ReminderService(service.store, replace(service.settings, business_id="business-b"), clock=service.clock)
    assert other.send_approved(token).status == "blocked"
    assert other.history() == []
    assert "Test Customer" not in other.history_csv().decode("utf-8-sig")


def test_approval_expiry(service, invoice):
    token = approve(service, invoice)
    now = service.clock()
    service.clock = lambda: now + timedelta(minutes=31)
    assert service.send_approved(token).status == "blocked"


def test_no_transport_when_initial_history_write_fails(service, invoice, monkeypatch):
    token = approve(service, invoice)
    service.transport = Mock()
    monkeypatch.setattr(service.store, "connect", Mock(side_effect=sqlite3.OperationalError("disk failure")))
    assert service.send_approved(token).status == "blocked"
    service.transport.assert_not_called()


def test_final_log_failure_keeps_durable_claim_and_blocks_retry(service, invoice, monkeypatch):
    token = approve(service, invoice)
    monkeypatch.setattr(service, "_finish", Mock(side_effect=sqlite3.OperationalError("disk full")))
    assert service.send_approved(token).status == "unknown"
    assert service.history()[0]["state"] == "claimed"
    assert service.send_approved(approve(service, invoice)).status == "blocked"


def test_unknown_transport_outcome_blocks_future_retry(service, invoice):
    service.transport = Mock(side_effect=TimeoutError())
    assert service.send_approved(approve(service, invoice)).status == "unknown"
    assert service.send_approved(approve(service, invoice)).status == "blocked"
    assert service.transport.call_count == 1


def test_definite_failure_is_recorded_and_allows_fresh_approval(service, invoice):
    service.transport = Mock(side_effect=DeliveryFailure("Rejected"))
    assert service.send_approved(approve(service, invoice)).status == "failed"
    service.transport = Mock()
    assert service.send_approved(approve(service, invoice)).status == "submitted"


@pytest.mark.parametrize("subject,body", [("", "Body"), ("Subject\r\nBcc: bad@example.com", "Body"), ("Subject", " ")])
def test_empty_or_header_injected_messages_cannot_be_approved(service, invoice, subject, body):
    with pytest.raises(ReminderError):
        approve(service, invoice, subject, body)


def test_csv_preserves_complete_message_and_neutralizes_formulas(service, invoice):
    token = approve(service, invoice, "=SUM(1,2)", "@formula\n" + "x"*300)
    rows = list(csv.DictReader(io.StringIO(service.history_csv().decode("utf-8-sig"))))
    assert rows[0]["subject"].startswith("'=")
    assert rows[0]["body"].startswith("'@")
    assert len(rows[0]["body"]) > 300


def test_selection_skips_contacted_invoices_and_reaches_next_batch(service, invoice):
    first = service.import_invoices([invoice])[0]
    second = service.import_invoices([{**invoice, "invoice_no": "INV-002"}])[0]
    assert service.send_approved(service.approve(first, "Subject", "Body")).status == "submitted"
    remaining, suppressed = service.select_overdue([first, second])
    assert [row["invoice_no"] for row in remaining] == ["INV-002"]
    assert suppressed == 1


def test_uncertain_attempt_needs_explicit_reconciliation_to_retry(service, invoice):
    service.transport = Mock(side_effect=TimeoutError())
    token = approve(service, invoice)
    assert service.send_approved(token).status == "unknown"
    service.reconcile(token, "failed", "Checked Gmail Sent mail and verified no message was submitted.")
    with service.store.connect() as db:
        assert db.execute("SELECT previous_state,outcome FROM reconciliations").fetchone()[:] == ("unknown", "failed")
    service.transport = Mock()
    assert service.send_approved(approve(service, invoice)).status == "submitted"


def test_active_claim_cannot_be_released(service, invoice):
    token = approve(service, invoice)
    service._claim(token)
    with pytest.raises(ReminderError, match="still be running"):
        service.reconcile(token, "failed", "Manually checked the account.")


def test_reconciliation_is_scoped_to_business(service, invoice):
    service.transport = Mock(side_effect=TimeoutError())
    token = approve(service, invoice)
    service.send_approved(token)
    other = ReminderService(service.store, replace(service.settings, business_id="other"), clock=service.clock)
    with pytest.raises(ReminderError):
        other.reconcile(token, "failed", "Manually checked the account.")
