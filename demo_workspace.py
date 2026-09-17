"""Opt-in demo login and an atomic, one-time fictional workspace dataset."""
import os
from contextlib import contextmanager
from datetime import timedelta
from zoneinfo import ZoneInfo

from accounts import Accounts, authorize
from business_features import BusinessFeatures
from ledger import Ledger
from scheduling import DEFAULT_TEMPLATES, Scheduling, Worker
from workspace_store import WorkspaceError, utcnow


DEMO_EMAIL = "demo@example.com"
SEED_ACTION = "demo.sample_data_loaded"


def demo_credentials():
    """Disabled by default; the password stays in server configuration."""
    if os.getenv("WORKSPACE_DEMO_LOGIN_ENABLED", "").strip().lower() != "true":
        return None
    if os.getenv("WORKSPACE_LIVE_ENABLED", "").strip().lower() == "true":
        return None
    password = os.getenv("WORKSPACE_DEMO_PASSWORD", "")
    return (DEMO_EMAIL, password) if password else None


def login_demo(store):
    credentials = demo_credentials()
    if not credentials:
        raise WorkspaceError("Demo sign-in is not enabled on this server.")
    accounts = Accounts(store)
    token = accounts.login(*credentials)
    try:
        companies = accounts.businesses(token)
        if len(companies) != 1 or companies[0]["role"] != "owner":
            raise WorkspaceError("The demo account must own exactly one demo workspace.")
        return token, companies[0]["id"]
    except Exception:
        accounts.logout(token)
        raise


class _SeedTransaction:
    """Let existing services share the seed's transaction without early commits."""
    def __init__(self, store, db):
        self.db, self.audit = db, store.audit

    @contextmanager
    def transaction(self):
        yield self.db


def seed_demo_workspace(store, token, business, *, now=None):
    """Load once into an empty demo workspace; reruns preserve all later edits."""
    now = now or utcnow()
    with store.transaction() as db:
        actor = authorize(db, token, business, "admin", now)
        email = db.execute("SELECT email FROM ws_users WHERE id=?", (actor,)).fetchone()[0]
        if email != DEMO_EMAIL:
            raise WorkspaceError("Sample setup is restricted to the demo account.")
        if db.execute("SELECT 1 FROM ws_audit WHERE business_id=? AND action=?",
                      (business, SEED_ACTION)).fetchone():
            return False
        if db.execute("SELECT 1 FROM ws_invoices WHERE business_id=?", (business,)).fetchone():
            raise WorkspaceError("Sample setup needs an empty demo workspace; existing invoices were preserved.")
        tz = db.execute("SELECT timezone FROM ws_businesses WHERE id=?", (business,)).fetchone()[0]
        today = now.astimezone(ZoneInfo(tz)).date()
        due = lambda days: (today + timedelta(days=days)).isoformat()
        shared = _SeedTransaction(store, db)
        clock = lambda: now
        ledger = Ledger(shared, token, business, clock)
        features = BusinessFeatures(shared, token, business, clock)
        schedules = Scheduling(shared, token, business, clock)

        # All names and transactions are fictional; addresses use example.com.
        examples = [
            ("001", "Sharma Electronics", "sharma", "45000", -30, "INR"),
            ("002", "Rathi Textiles", "rathi", "62500", -15, "INR"),
            ("003", "Kiran Auto Components", "kiran", "78000", -45, "INR"),
            ("004", "Mehta Packaging", "mehta", "31000", 10, "INR"),
            ("005", "Desai Engineering", "desai", "95000", -75, "INR"),
            ("006", "Patil Foods", "patil", "24000", -10, "INR"),
            ("007", "Sahyadri Logistics", "sahyadri", "36000", -105, "INR"),
            ("008", "Deccan Design Studio", "deccan", "1200", -12, "USD"),
            ("009", "Sharma Electronics", "sharma", "18000", 5, "INR"),
            ("010", "Mehta Packaging", "mehta", "15000", -3, "INR"),
        ]
        ledger.import_records([
            dict(invoice_no="DEMO-" + number, client_name=name, email=email + "@example.com",
                 amount=total, amount_paid="0", due_date=due(days), currency=currency,
                 status="unpaid", notes="Fictional sample invoice for product demonstrations.")
            for number, name, email, total, days, currency in examples
        ])
        for number, *_ in examples:
            ledger.update_collection("DEMO-" + number, consents=["email"], owner="Demo collector",
                                     note="Sample customer; email permission is illustrative.")
        # Received dates use UTC, matching the ledger's future-date validation.
        for number, value, kind, days in [
            ("002", "25000", "payment", 4),
            ("005", "5000", "credit", 2),
            ("006", "24000", "payment", 1),
            ("008", "200", "payment", 3),
        ]:
            ledger.receipt("DEMO-" + number, value, kind, "DEMO-" + kind.upper() + "-" + number,
                           (now.date() - timedelta(days=days)).isoformat())
        ledger.update_collection("DEMO-003", consents=["email"], owner="Demo collector",
                                 status="disputed", note="Sample dispute: customer is checking the supplied quantity.")
        ledger.update_collection("DEMO-005", consents=["email"], owner="Demo collector",
                                 promise_date=due(5), note="Sample promise: customer expects to pay in five days.")
        ledger.update_collection("DEMO-007", consents=["email"], owner="Demo collector",
                                 status="on_hold", note="Sample hold: waiting for a purchase order correction.")
        ledger.update_collection("DEMO-010", consents=["email"], opted_out=True,
                                 note="Sample opt-out: automatic reminders are suppressed.")
        features.save_installments("DEMO-002", [
            {"due_date": due(-15), "amount": "25000"},
            {"due_date": due(7), "amount": "18750"},
            {"due_date": due(21), "amount": "18750"},
        ])
        for language, (subject, body) in DEFAULT_TEMPLATES.items():
            schedules.save_template("Sample " + ("English" if language == "en" else "Hindi") + " reminder",
                                    language, subject, body)
        schedules.save_rule("Sample email follow-up (paused)", channel="email",
                            days=[-3, 0, 7, 14, 30], mode="dry_run", auto_send=False, active=False)
        # Exercise the real preview path; never fabricate sent/delivered history.
        draft = schedules.draft("DEMO-001")
        job = schedules.approve_manual(draft, draft["subject"], draft["body"], "dry_run")
        result = Worker(shared, None, clock=clock, live_enabled=False).run_one(job, business)
        if not result or result["state"] != "previewed":
            raise WorkspaceError("Sample reminder preview failed; no sample data was saved.")
        store.audit(db, business, actor, SEED_ACTION, {"customers": 8, "invoices": 10}, now)
    return True


def main():
    import argparse
    from pathlib import Path
    import config
    from workspace_store import WorkspaceStore
    from workspace_storage import database_location

    parser = argparse.ArgumentParser(description="Add fictional sample data to the configured demo account")
    parser.add_argument("--database", help="SQLite file path or hosted libSQL URL")
    args = parser.parse_args()
    store = WorkspaceStore(args.database or database_location())
    token = None
    try:
        token, business = login_demo(store)
        added = seed_demo_workspace(store, token, business)
        print("Added 8 sample customers, 10 invoices, 4 receipts/credits, an installment plan, "
              "2 templates, a paused schedule and a reminder preview." if added else
              "Sample data is already loaded; existing records and edits were preserved.")
    except WorkspaceError as exc:
        parser.error(str(exc))
    finally:
        if token:
            Accounts(store).logout(token)


if __name__ == "__main__":
    main()
