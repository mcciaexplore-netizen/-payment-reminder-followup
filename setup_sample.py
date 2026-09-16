"""Sample workbook generation; importing this module never writes files."""
import argparse
import io
from datetime import date, timedelta
from pathlib import Path
import pandas as pd


def sample_bytes():
    today = date.today()
    data = {
        "Invoice No": ["INV-001", "INV-002", "INV-003", "INV-004", "INV-005", "INV-006"],
        "Client Name": ["Sharma Electronics", "Rathi Textiles", "Kiran Auto", "Mehta Packaging", "Desai Engineering", "Patil Foods"],
        "Email": [f"accounts{i}@example.com" for i in range(1, 7)],
        "Amount": [45000, 12500, 78000, 31000, 19500, 62000],
        "Amount Paid": [0, 2000, 0, 0, 0, 0],
        "Currency": ["INR"] * 6,
        "Due Date": [(today-timedelta(days=n)).isoformat() for n in [30, 15, 40, -10, 8, 3]],
        "Status": ["Unpaid", "Partially Paid", "Unpaid", "Unpaid", "Unpaid", "Unpaid"],
        "Notes": ["", "Partial payment received", "", "", "", ""],
    }
    buffer = io.BytesIO()
    pd.DataFrame(data).to_excel(buffer, index=False)
    return buffer.getvalue()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create a sample workbook using example.com addresses.")
    parser.add_argument("--output", default="invoices.xlsx")
    args = parser.parse_args()
    try:
        with Path(args.output).open("xb") as output:
            output.write(sample_bytes())
    except FileExistsError:
        parser.error("The output file exists. Choose another --output path.")
    print(f"Created {args.output}. Expected: four overdue invoices, INR 153,000 outstanding.")
