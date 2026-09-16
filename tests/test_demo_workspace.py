from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from accounts import Accounts
from business_features import BusinessFeatures
from demo_workspace import DEMO_EMAIL, demo_credentials, login_demo, seed_demo_workspace
from ledger import Ledger
from scheduling import Scheduling
from workspace_store import WorkspaceError, WorkspaceStore


PASSWORD = "test demo password long"


@pytest.fixture
def demo(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DEMO_LOGIN_ENABLED", "true")
    monkeypatch.setenv("WORKSPACE_DEMO_PASSWORD", PASSWORD)
    monkeypatch.setenv("WORKSPACE_LIVE_ENABLED", "false")
    store = WorkspaceStore(tmp_path / "demo.sqlite3")
    token, business = Accounts(store).bootstrap(DEMO_EMAIL, PASSWORD, "Demo business")
    return store, token, business


def test_sample_balances_and_preview_are_real_service_results(demo):
    store, token, business = demo
    # UTC and business calendar differ here; receipts must never be future-dated.
    now = datetime.now(timezone.utc).replace(hour=23, minute=0, second=0)
    token = Accounts(store, clock=lambda: now).login(DEMO_EMAIL, PASSWORD)
    assert seed_demo_workspace(store, token, business, now=now)
    ledger = Ledger(store, token, business)
    rows = {r["invoice_no"]: r for r in ledger.invoices()}
    assert len(rows) == 10
    assert len({r["email"] for r in rows.values()}) == 8
    assert all(r["email"].endswith("@example.com") for r in rows.values())
    assert rows["DEMO-002"]["outstanding_amount"] == "37500.00"
    assert rows["DEMO-006"]["status"] == "paid"
    assert rows["DEMO-003"]["status"] == "disputed"
    assert rows["DEMO-007"]["status"] == "on_hold"
    assert rows["DEMO-010"]["opted_out"]
    assert len(ledger.receipts()) == 4
    assert ledger.reports()["INR"]["outstanding"] == "350500.00"
    assert ledger.reports()["USD"]["outstanding"] == "1000.00"
    assert len(BusinessFeatures(store, token, business).installments("DEMO-002")) == 2
    schedules = Scheduling(store, token, business)
    assert len(schedules.templates()) == 2
    assert not schedules.rules()[0]["active"]
    assert [(j["mode"], j["state"]) for j in schedules.jobs()] == [("dry_run", "previewed")]


def test_repeated_seed_preserves_user_changes(demo):
    store, token, business = demo
    assert seed_demo_workspace(store, token, business)
    ledger = Ledger(store, token, business)
    ledger.update_collection("DEMO-001", note="Edited during the demo")
    before = ledger.invoices(), ledger.receipts(), ledger.audit()
    assert not seed_demo_workspace(store, token, business)
    assert (ledger.invoices(), ledger.receipts(), ledger.audit()) == before


def test_failed_seed_rolls_back_all_records(demo):
    store, token, business = demo
    with patch("demo_workspace.Worker.run_one", side_effect=WorkspaceError("Preview failed")):
        with pytest.raises(WorkspaceError, match="Preview failed"):
            seed_demo_workspace(store, token, business)
    assert Ledger(store, token, business).invoices() == []
    assert Ledger(store, token, business).receipts() == []
    assert Scheduling(store, token, business).templates() == []
    assert Scheduling(store, token, business).jobs() == []
    assert seed_demo_workspace(store, token, business)


def test_seed_rejects_non_demo_account_and_foreign_business(demo, tmp_path):
    store, token, business = demo
    other_store = WorkspaceStore(tmp_path / "other.sqlite3")
    other_token, other_business = Accounts(other_store).bootstrap("owner@example.com", PASSWORD, "Real business")
    with pytest.raises(WorkspaceError, match="restricted to the demo"):
        seed_demo_workspace(other_store, other_token, other_business)
    with pytest.raises(WorkspaceError, match="session or role"):
        seed_demo_workspace(store, token, other_business)


def test_seed_never_overwrites_existing_invoices(demo):
    store, token, business = demo
    ledger = Ledger(store, token, business)
    ledger.import_records([dict(invoice_no="EXISTING", client_name="Existing customer",
                                email="existing@example.com", amount="150", due_date="2026-09-01", status="unpaid")])
    with pytest.raises(WorkspaceError, match="existing invoices were preserved"):
        seed_demo_workspace(store, token, business)
    assert [r["invoice_no"] for r in ledger.invoices()] == ["EXISTING"]


def test_demo_login_requires_opt_in_password_and_preview_server(demo, monkeypatch):
    store, _, business = demo
    token, selected = login_demo(store)
    assert selected == business
    Accounts(store).logout(token)
    monkeypatch.setenv("WORKSPACE_DEMO_PASSWORD", "incorrect test password")
    with pytest.raises(WorkspaceError, match="incorrect"):
        login_demo(store)
    monkeypatch.setenv("WORKSPACE_DEMO_LOGIN_ENABLED", "false")
    assert demo_credentials() is None
    with pytest.raises(WorkspaceError, match="not enabled"):
        login_demo(store)
    monkeypatch.setenv("WORKSPACE_DEMO_LOGIN_ENABLED", "true")
    monkeypatch.setenv("WORKSPACE_LIVE_ENABLED", "true")
    assert demo_credentials() is None


def test_demo_login_rejects_multiple_businesses_and_revokes_attempt_session(demo):
    store, token, _ = demo
    accounts = Accounts(store)
    accounts.create_business(token, "Second business")
    with pytest.raises(WorkspaceError, match="exactly one"):
        login_demo(store)
    with store.transaction() as db:
        assert db.execute("SELECT count(*) FROM ws_sessions").fetchone()[0] == 1
