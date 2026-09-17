import sys
from pathlib import Path
from datetime import datetime, timezone
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import SendSettings
from reminders import ReminderService, ReminderStore


@pytest.fixture
def invoice():
    return dict(invoice_no="INV-001", client_name="Test Customer", email="customer@example.com",
                amount="12500", amount_paid="2000", due_date="2026-08-01", status="Partially Paid", currency="INR")


@pytest.fixture
def service(tmp_path):
    return ReminderService(ReminderStore(tmp_path / "history.sqlite3"),
        SendSettings(business_id="business-a", sender="sender@example.com", password="test-secret", dry_run=False, preview_only=False),
        transport=lambda *args: None, clock=lambda: datetime(2026, 9, 15, tzinfo=timezone.utc))
