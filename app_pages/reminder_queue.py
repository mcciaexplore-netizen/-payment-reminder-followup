import json
import streamlit as st
from ledger import csv_bytes
from workspace_ui import context,invoice_picker,require_role,notify

ctx=context()
jobs=ctx["scheduling"].jobs()
st.caption("Review your message before processing it. Previews, submissions and delivery updates appear in the history below.")
fields=("id","invoice_key","channel","mode","state","created","attempted_at","detail")
with st.expander(f"Reminder history · {len(jobs)}",expanded=bool(jobs)):
    if jobs:
        st.dataframe([{ "Invoice":j["invoice_key"],"Channel":j["channel"].title(),
                        "Mode":"Preview" if j["mode"]=="dry_run" else "Live",
                        "Status":j["state"].replace("_"," ").title(),"Details":j["detail"]} for j in jobs],hide_index=True,row_height=38)
    else:
        st.info("No reminders yet. Start by preparing a draft below.")
    st.download_button("Export reminder history",csv_bytes([{k:j[k] for k in fields} for j in jobs]),file_name="reminders.csv",icon=":material/download:")
require_role(ctx,"collect")
tab=st.segmented_control("Work on",["New reminder","Awaiting review","Recovery"],default="New reminder")
if tab=="New reminder":
    inv=invoice_picker(ctx["ledger"].invoices(),key="reminder_invoice")
    channel=st.selectbox("Channel",["email","whatsapp","sms"])
    templates=ctx["scheduling"].templates()
    template=st.selectbox("Template",[None]+[r["id"] for r in templates],format_func=lambda x:"Business default" if x is None else next(r["name"] for r in templates if r["id"]==x))
    if st.button("Prepare draft"):
        st.session_state.draft_manual=ctx["scheduling"].draft(inv["invoice_no"],channel,template)
        st.session_state.draft_business=ctx["business"]
    draft=st.session_state.get("draft_manual")
    if draft and st.session_state.get("draft_business")==ctx["business"]:
        st.subheader(f"Review {draft['invoice']['invoice_no']} · {draft['channel']}")
        st.write("Recipient: "+draft["recipient"])
        with st.form("manual_review_"+str(draft["invoice"]["revision"])+draft["invoice_key"]+draft["channel"]):
            subject=st.text_input("Subject",value=draft["subject"])
            body=st.text_area("Message",value=draft["body"],height=240)
            mode=st.selectbox("Sending mode",["dry_run","live"],format_func=lambda x:"Preview only" if x=="dry_run" else "Live submission")
            approved=st.checkbox("I approve this recipient and exact message")
            submit=st.form_submit_button("Approve and process",type="primary")
        if submit:
            if not approved:
                st.warning("Approve the reviewed message before processing it.")
            else:
                job=ctx["scheduling"].approve_manual(draft,subject,body,mode)
                result=ctx["worker"].run_one(job,ctx["business"])
                del st.session_state.draft_manual
                notify("Reminder result: "+(result["state"] if result else "Already claimed by the background worker"))
elif tab=="Awaiting review":
    pending=[r for r in jobs if r["state"]=="awaiting_approval"]
    if not pending:
        st.info("No scheduled drafts are waiting for review.")
    else:
        selected=st.selectbox("Draft",[r["id"] for r in pending],format_func=lambda x:next(r["invoice_key"]+" · "+r["channel"] for r in pending if r["id"]==x))
        job=next(r for r in pending if r["id"]==selected)
        payload=json.loads(job["payload"])
        st.write(f"Recipient: {payload['recipient']} · Mode: {job['mode']}")
        with st.form("scheduled_review_"+selected):
            subject=st.text_input("Subject",value=payload["subject"])
            body=st.text_area("Message",value=payload["body"],height=240)
            approved=st.checkbox("I approve this exact reminder")
            submit=st.form_submit_button("Approve scheduled reminder")
        if submit and approved:
            ctx["scheduling"].approve_job(selected,subject,body)
            notify("Reminder approved. The worker will process it during the permitted sending window.")
        if st.button("Cancel this draft"):
            ctx["scheduling"].cancel_job(selected)
            notify("Draft cancelled.")
else:
    require_role(ctx,"manage")
    uncertain=[j for j in jobs if j["state"] in {"claimed","unknown"} and j["mode"]=="live"]
    if uncertain:
        selected=st.selectbox("Uncertain attempt",[r["id"] for r in uncertain])
        outcome=st.selectbox("Verified outcome",["submitted","failed"])
        note=st.text_input("How did you verify the provider’s result?")
        confirmed=st.checkbox("I checked the provider and confirmed no worker is still processing this attempt")
        if st.button("Record verified outcome") and confirmed:
            ctx["scheduling"].reconcile(selected,outcome,note)
            notify("Verified outcome recorded. Any retry requires a fresh approval.")
    else:
        st.info("No uncertain live attempts need reconciliation.")
queued=[j for j in jobs if j["state"]=="queued"]
if queued:
    with st.expander("Cancel queued work"):
        selected=st.selectbox("Queued reminder",[j["id"] for j in queued])
        if st.button("Cancel queued reminder"):
            ctx["scheduling"].cancel_job(selected)
            notify("Queued reminder cancelled.")
