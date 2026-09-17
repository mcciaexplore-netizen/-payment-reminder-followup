import io
from datetime import date,datetime,timedelta
from zoneinfo import ZoneInfo
import streamlit as st
from invoices import read_invoice_file,REQUIRED
from ledger import csv_bytes
from setup_sample import sample_bytes
from workspace_ui import context,invoice_picker,notify,require_role
from ui_design import invoice_table

ctx=context()
rows=ctx["ledger"].invoices()
today=datetime.now(ZoneInfo(ctx["company"]["timezone"])).date()
search,status_filter,currency_filter=st.columns([2.5,1.3,1])
with search:
    query=st.text_input("Search invoices or customers",placeholder="Invoice number, customer name or email")
with status_filter:
    status=st.selectbox("Show invoices",["All statuses","Overdue","Due in 7 days","Paid","Disputed","On hold","Cancelled"])
with currency_filter:
    currency=st.selectbox("Currency",["All currencies"]+sorted({r["currency"] for r in rows}))
view=[r for r in rows if query.casefold() in (r["invoice_no"]+" "+r["client_name"]+" "+r["email"]).casefold()]
if currency!="All currencies":
    view=[r for r in view if r["currency"]==currency]
if status=="Overdue":
    view=[r for r in view if r["status"] in {"unpaid","partially_paid"} and date.fromisoformat(r["due_date"])<today]
elif status=="Due in 7 days":
    view=[r for r in view if r["status"] in {"unpaid","partially_paid"} and today<=date.fromisoformat(r["due_date"])<=today+timedelta(days=7)]
elif status!="All statuses":
    view=[r for r in view if r["status"]=={"Paid":"paid","Disputed":"disputed","On hold":"on_hold","Cancelled":"cancelled"}[status]]
fields=("invoice_no","client_name","email","currency","amount","amount_paid","outstanding_amount","due_date","status","owner","promise_date")
with st.container(border=True):
    with st.container(horizontal=True,horizontal_alignment="distribute",vertical_alignment="center"):
        st.subheader("Invoice book")
        st.download_button("Export invoices",csv_bytes([{k:r[k] for k in fields} for r in view]),file_name="invoices.csv",mime="text/csv",icon=":material/download:")
    st.caption(f"{len(view)} of {len(rows)} invoices · Amounts are shown in each invoice’s currency.")
    invoice_table(view,today=today)
if ctx["company"]["role"] in {"owner","manager"}:
    with st.expander("Import Excel or CSV",expanded=not rows):
        st.download_button("Download sample workbook",sample_bytes(),file_name="sample_invoices.xlsx")
        upload=st.file_uploader("Invoice file",type=["csv","xlsx"])
        if upload:
            headers=read_invoice_file(upload,headers_only=True)
            custom=st.checkbox("Map my column names")
            mapping=None
            if custom:
                mapping={}
                for field in sorted(REQUIRED)+["amount_paid","currency","notes"]:
                    options=["Skip"]+headers if field not in REQUIRED else ["Choose column"]+headers
                    source=st.selectbox(field.replace("_"," ").title(),options,index=options.index(field) if field in options else 0,key="import_map_"+field)
                    if source in headers:
                        if source in mapping:
                            st.error("Each source column can be used only once.")
                            st.stop()
                        mapping[source]=field
            if st.button("Validate and import",type="primary"):
                records=read_invoice_file(upload,column_mapping=mapping,default_currency=ctx["features"].branding()["default_currency"])
                count=ctx["ledger"].import_records(records)
                notify(f"Imported {count} invoices. Contact permissions start off until you record them below.")
require_role(ctx,"collect")
st.subheader("Customer follow-up details")
inv=invoice_picker(rows,key="invoice_details")
with st.form("collection_"+inv["invoice_no"]):
    phone=st.text_input("Phone with country code",value=inv["phone"])
    consents=st.multiselect("Permitted reminder channels",["email","sms","whatsapp"],default=inv["consents"])
    st.caption("Select only channels the customer has agreed to use for payment reminders.")
    opted_out=st.checkbox("Customer opted out of all reminders",value=inv["opted_out"])
    language=st.selectbox("Language",["en","hi"],index=0 if inv["language"]=="en" else 1,format_func=lambda x:{"en":"English","hi":"Hindi"}[x])
    status=st.selectbox("Collection status",["active","disputed","on_hold","cancelled"],index=["active","disputed","on_hold","cancelled"].index(inv["status"] if inv["status"] in {"disputed","on_hold","cancelled"} else "active"))
    owner=st.text_input("Assigned collector",value=inv["owner"])
    promised=st.checkbox("Pause through a promised payment date",value=bool(inv["promise_date"]))
    promise=st.date_input("Promised payment date",value=date.fromisoformat(inv["promise_date"]) if inv["promise_date"] else date.today())
    note=st.text_area("Internal follow-up note",value=inv["followup_note"])
    save=st.form_submit_button("Save follow-up details")
if save:
    ctx["ledger"].update_collection(inv["invoice_no"],phone=phone,consents=consents,opted_out=opted_out,language=language,status=status,owner=owner,promise_date=promise.isoformat() if promised else "",note=note)
    notify("Follow-up details saved. Existing drafts must be reviewed again.")
with st.expander("Customer access link"):
    st.caption("A link shows this customer’s invoices and payment links. Anyone with the link can view them until it expires or is revoked.")
    days=st.number_input("Access duration in days",1,30,7)
    if st.button("Create customer link"):
        import os
        token=ctx["features"].create_portal(inv["email"],int(days))
        base=os.getenv("PUBLIC_WEBHOOK_BASE_URL","http://127.0.0.1:8503").rstrip("/")
        st.code(base+"/portal/"+token,language=None)
        st.caption("Copy this link now. It is not sent automatically. The customer access service must be running.")
    if st.button("Revoke this customer’s links"):
        ctx["features"].revoke_portals(inv["email"])
        notify("Customer access links revoked.")
