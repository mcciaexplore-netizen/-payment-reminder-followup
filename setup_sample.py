"""
setup_sample.py
Creates a sample invoices.xlsx for testing the free-tier agent.
Run once before running the agent:

  python setup_sample.py
  python agent.py
"""

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

T = date.today()
ago  = lambda n: (T - timedelta(days=n)).isoformat()
ahead = lambda n: (T + timedelta(days=n)).isoformat()

DATA = {
    "Invoice No": [
        "INV-2025-041", "INV-2025-042", "INV-2025-043",
        "INV-2025-044", "INV-2025-045", "INV-2025-046",
    ],
    "Client Name": [
        "Sharma Electronics Pvt Ltd",
        "Rathi Textile Mills",
        "Kiran Auto Components",
        "Mehta Packaging Works",
        "Desai Engineering Co",
        "Patil Food Products",
    ],
    "Email": [
        "accounts@sharmaelectronics.com",
        "finance@rathitextiles.in",
        "purchase@kiranauto.co.in",
        "mehta.packaging@gmail.com",
        "desaieng@outlook.com",
        "patilfoods.accounts@gmail.com",
    ],
    "Amount": [45000, 12500, 78000, 31000, 19500, 62000],
    "Invoice Date": [
        ago(60), ago(45), ago(30), ago(20), ago(10), ago(5),
    ],
    "Due Date": [
        ago(30),    # 30d overdue  → reminder
        ago(15),    # 15d overdue  → reminder
        ago(40),    # 40d overdue  → reminder (most urgent)
        ahead(10),  # not due yet  → skipped
        ago(8),     #  8d overdue  → reminder
        ago(3),     #  3d overdue  → below default 7d threshold
    ],
    "Status": ["Unpaid"] * 5 + ["Unpaid"],
    "Notes": [
        "Second reminder needed",
        "Partial ₹2,000 received on " + ago(10),
        "",
        "PO raised, payment pending approval",
        "",
        "New client — first invoice",
    ],
}

out = Path("invoices.xlsx")
pd.DataFrame(DATA).to_excel(out, index=False)
print(f"Created {out} — {len(DATA['Invoice No'])} records.")
print()
print("Expected with default --days 7 threshold:")
print("  INV-2025-041 — 30d overdue  → reminder")
print("  INV-2025-042 — 15d overdue  → reminder")
print("  INV-2025-043 — 40d overdue  → reminder  ← most urgent")
print("  INV-2025-044 — not due yet  → skipped")
print("  INV-2025-045 —  8d overdue  → reminder")
print("  INV-2025-046 —  3d overdue  → below threshold, skipped")
