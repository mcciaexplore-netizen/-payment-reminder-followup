from datetime import date
import pandas as pd
import streamlit as st
from ledger import amount,csv_bytes
from workspace_ui import context,invoice_picker,require_role,notify

ctx=context()
receipts=ctx["ledger"].receipts()
invoices=ctx["ledger"].invoices()
currencies={r["invoice_no"].casefold():r["currency"] for r in invoices}
st.caption("Receipts and credits reduce the balance immediately. Corrections use an audited reversal.")
display=[{"id":r["id"],"invoice":r["invoice_key"],"currency":currencies[r["invoice_key"]],"amount":amount(r["amount"]),"kind":r["kind"],"reference":r["reference"],"date":r["received_on"]} for r in receipts]
with st.container(border=True):
    with st.container(horizontal=True,horizontal_alignment="distribute",vertical_alignment="center"):
        st.subheader("Payment activity")
        st.download_button("Export receipts",csv_bytes(display),file_name="receipts.csv",icon=":material/download:")
    if display:
        st.dataframe([{**r,"amount":float(r["amount"]),"kind":r["kind"].title(),"date":date.fromisoformat(r["date"])} for r in display],hide_index=True,
                     column_order=["invoice","date","kind","currency","amount","reference"],row_height=38,
                     column_config={"invoice":"Invoice","date":st.column_config.DateColumn("Received",format="DD MMM YYYY"),
                                    "kind":"Type","currency":"Currency","amount":st.column_config.NumberColumn("Amount",format="%,.2f"),"reference":"Reference"})
    else:
        st.info("No payments recorded yet. Your receipts and credits will appear here.")
require_role(ctx,"collect")
inv=invoice_picker(invoices,key="payment_invoice")
st.subheader("Record a receipt or credit")
with st.form("receipt_form"):
    left,right=st.columns(2)
    with left:
        value=st.text_input(f"Amount ({inv['currency']})")
        reference=st.text_input("Unique receipt or credit reference")
    with right:
        kind=st.selectbox("Type",["payment","credit"])
        received=st.date_input("Date received",value=date.today(),max_value=date.today())
    record=st.form_submit_button("Record receipt",type="primary")
if record:
    ctx["ledger"].receipt(inv["invoice_no"],value,kind,reference,received.isoformat())
    notify("Receipt recorded and invoice balance updated.")
with st.expander("Payment links"):
    st.caption("The configured payment gateway creates links for the exact outstanding amount. Uncertain requests must be reconciled before another link is created.")
    if st.button("Create or retrieve payment link"):
        link=ctx["connectors"].create_link(ctx["token"],ctx["business"],inv["invoice_no"])
        st.link_button("Open payment link",link["url"])
    links=ctx["connectors"].links(ctx["token"],ctx["business"])
    filtered=[r for r in links if r["invoice_key"]==inv["invoice_no"].casefold()]
    st.dataframe([{k:r[k] for k in ("id","amount","currency","state","url")} for r in filtered],hide_index=True,column_config={"url":st.column_config.LinkColumn("Payment link"),"amount":"Amount in minor units"})
    if filtered and ctx["company"]["role"] in {"owner","manager"}:
        link_id=st.selectbox("Link to check",[r["id"] for r in filtered])
        if st.button("Check gateway and reconcile link"):
            result=ctx["connectors"].reconcile_link(ctx["token"],ctx["business"],link_id)
            notify("Gateway result recorded: "+result["state"])
require_role(ctx,"manage")
with st.expander("Installment plan"):
    st.caption("Enter the full invoice amount across installments, oldest first. Existing receipts are allocated to the oldest amounts automatically.")
    st.dataframe(ctx["features"].installments(inv["invoice_no"]),hide_index=True)
    plan=st.data_editor(pd.DataFrame([{"due_date":inv["due_date"],"amount":inv["amount"]}]),num_rows="dynamic",key="installment_editor_"+inv["invoice_no"])
    if st.button("Save installment plan"):
        ctx["features"].save_installments(inv["invoice_no"],plan.to_dict("records"))
        notify("Installment plan saved. Future reminders follow the oldest unpaid installment.")
if receipts:
    with st.expander("Reverse a receipt"):
        originals=[r for r in receipts if r["amount"]>0]
        if originals:
            selected=st.selectbox("Receipt to reverse",[r["id"] for r in originals],format_func=lambda x:next(r["reference"] for r in originals if r["id"]==x))
            reason=st.text_input("Reason for reversal")
            confirmed=st.checkbox("I have verified this receipt should be reversed")
            if st.button("Record reversal"):
                if not confirmed:
                    st.error("Confirm the reversal before proceeding.")
                else:
                    ctx["ledger"].reverse_receipt(selected,reason)
                    notify("Reversal recorded. The original receipt remains in the audit history.")
