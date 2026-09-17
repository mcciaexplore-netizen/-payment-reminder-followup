"""Shared presentation helpers. Amounts remain exact until chart/table display."""
from datetime import date
from decimal import Decimal
from html import escape

import streamlit as st


PAGE_DESCRIPTIONS = {
    "Overview": "Your receivables at a glance. See what is due and where to focus next.",
    "Invoices & customers": "A clear record of every invoice, customer and follow-up.",
    "Payments & plans": "Record collections, track credits and manage payment arrangements.",
    "Reminders": "Prepare, review and track each customer follow-up.",
    "Schedules & templates": "Set the right timing and tone for your payment reminders.",
    "Reports & history": "Understand your collections and follow the history behind each balance.",
    "Business settings": "Manage your business identity, payment details and account preferences.",
    "Team & integrations": "Give your team the right access and connect your business tools.",
}


def page_heading(title, business_name, today):
    st.html(f'<div class="workspace-topline"><span class="workspace-kicker">'
            f'{escape(business_name)} / Payment workspace</span><span>{today:%d %b %Y}</span></div>')
    st.title(title)
    st.html(f'<p class="workspace-description">{escape(PAGE_DESCRIPTIONS.get(title, ""))}</p>')


def money_display(value, currency):
    value = Decimal(str(value))
    whole, fraction = f"{abs(value):.2f}".split(".")
    if currency == "INR" and len(whole) > 3:
        leading, tail = whole[:-3], whole[-3:]
        groups = []
        while leading:
            groups.insert(0, leading[-2:])
            leading = leading[:-2]
        whole = ",".join(groups + [tail])
    else:
        whole = f"{int(whole):,}"
    decimals = "." + fraction if fraction != "00" else ""
    symbol = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}.get(currency, currency + " ")
    return ("−" if value < 0 else "") + symbol + whole + decimals


def invoice_status(invoice, today=None):
    today = today or date.today()
    status = invoice["status"]
    if status in {"unpaid", "partially_paid"} and date.fromisoformat(invoice["due_date"]) < today:
        return "Part-paid · overdue" if status == "partially_paid" else "Overdue"
    return {"unpaid": "Upcoming", "partially_paid": "Part-paid", "on_hold": "On hold",
            "paid": "Paid", "disputed": "Disputed", "cancelled": "Cancelled"}.get(status, status.title())


def invoice_table(rows, *, today=None, compact=False):
    """Show readable columns while keeping machine-readable exports separate."""
    import pandas as pd
    records = [{"Invoice": r["invoice_no"], "Customer": r["client_name"],
                "Due date": date.fromisoformat(r["due_date"]), "Currency": r["currency"],
                "Outstanding": float(r["outstanding_amount"]), "Status": invoice_status(r, today),
                "Invoice total": float(r["amount"]), "Paid": float(r["amount_paid"]),
                "Email": r["email"]} for r in rows]
    if not records:
        st.info("No invoices match these filters.")
        return
    frame = pd.DataFrame(records)
    def status_style(value):
        colors = {"Paid": ("#edf7f1", "#236244"), "Overdue": ("#fff3e8", "#915017"),
                  "Part-paid · overdue": ("#fff3e8", "#915017"), "Disputed": ("#fceff0", "#a13d48"),
                  "On hold": ("#f0f2f5", "#596778")}
        bg, ink = colors.get(value, ("#edf5fb", "#235f8c"))
        return f"background-color: {bg}; color: {ink};"
    st.dataframe(frame.style.map(status_style, subset=["Status"]), hide_index=True,
                 height="auto" if compact else min(550, 44 + len(rows) * 38), row_height=38,
                 column_order=["Invoice", "Customer", "Due date", "Currency", "Outstanding", "Status"],
                 column_config={"Invoice": st.column_config.TextColumn(width=110, pinned=True),
                                "Customer": st.column_config.TextColumn(width=180),
                                "Due date": st.column_config.DateColumn(format="DD MMM YYYY", width=115),
                                "Currency": st.column_config.TextColumn(width=75),
                                "Outstanding": st.column_config.NumberColumn(format="%,.2f", width=140),
                                "Status": st.column_config.TextColumn(width=150)})


def status_list(items):
    lines = "".join(f'<div class="status-line"><dt>{escape(label)}</dt><dd>{escape(str(value))}</dd></div>'
                    for label, value in items)
    st.html('<dl class="status-list">' + lines + '</dl>')
