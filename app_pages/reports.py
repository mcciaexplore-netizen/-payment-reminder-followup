import pandas as pd
import streamlit as st
from ledger import csv_bytes,amount
from workspace_ui import context
from ui_design import invoice_table

ctx=context()
choice=st.segmented_control("Report",["Customer statements","Cash-flow estimate","Collections","Audit history"],default="Customer statements")
if choice=="Customer statements":
    invoices=ctx["ledger"].invoices()
    customers=sorted({r["email"] for r in invoices if r["email"]})
    if not customers:
        st.info("Import customer invoices to view statements.")
    else:
        email=st.selectbox("Customer email",customers)
        fields=("invoice_no","client_name","currency","amount","amount_paid","outstanding_amount","due_date","status")
        statement=ctx["ledger"].statement(email)
        rows=[{k:r[k] for k in fields} for r in statement]
        invoice_table(statement)
        st.download_button("Download customer statement",csv_bytes(rows),file_name="customer_statement.csv")
elif choice=="Cash-flow estimate":
    rows=ctx["features"].forecast()
    st.caption("A planning estimate, not a payment guarantee. Dates use customer promises, then installment/due dates plus the median delay in recorded, unreversed payments. Past estimates are moved to today.")
    if rows:
        frame=pd.DataFrame(rows)
        frame["amount"]=frame["amount"].astype(float)
        for currency in frame["currency"].unique():
            st.subheader(currency)
            st.bar_chart(frame[frame["currency"]==currency].groupby("expected_date",as_index=False)["amount"].sum(),x="expected_date",y="amount")
    st.dataframe(rows,hide_index=True)
    st.download_button("Export cash-flow estimate",csv_bytes(rows),file_name="cash_flow_estimate.csv")
elif choice=="Collections":
    invoices={r["invoice_no"].casefold():r for r in ctx["ledger"].invoices()}
    rows=[{"invoice":r["invoice_key"],"currency":invoices[r["invoice_key"]]["currency"],"date":r["received_on"],"amount":amount(r["amount"]),"type":r["kind"],"reference":r["reference"]} for r in ctx["ledger"].receipts()]
    st.caption("Recorded receipts, credits and reversals. Opening paid balances from imports are shown on invoices and are not invented as dated receipts.")
    st.dataframe(rows,hide_index=True)
    st.download_button("Export collections",csv_bytes(rows),file_name="collections.csv")
else:
    rows=ctx["ledger"].audit()
    st.dataframe(rows,hide_index=True)
    st.download_button("Export audit history",csv_bytes(rows),file_name="audit_history.csv")
