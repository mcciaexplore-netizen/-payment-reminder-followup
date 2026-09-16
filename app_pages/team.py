import streamlit as st
from workspace_ui import context,require_role,notify

ctx=context()
require_role(ctx,"admin")
st.subheader("Team access")
st.caption("Owners manage access and connectors. Managers configure policies and import invoices. Collectors record receipts and approve reminders. Viewers read reports.")
members=ctx["accounts"].members(ctx["token"],ctx["business"])
st.dataframe(members,hide_index=True,column_order=["email","role"],column_config={"email":"Team member","role":"Access level"},row_height=38)
with st.form("invite"):
    email=st.text_input("Invite email")
    role=st.selectbox("Role",["viewer","collector","manager","owner"])
    invite=st.form_submit_button("Create invitation")
if invite:
    code=ctx["accounts"].invite(ctx["token"],ctx["business"],email,role)
    st.code(code,language=None)
    st.caption("Share this one-time code securely. It expires in 72 hours. The recipient chooses Use invitation on the sign-in page.")
with st.expander("Change or remove a member"):
    selected=st.selectbox("Member",[m["id"] for m in members],format_func=lambda x:next(m["email"] for m in members if m["id"]==x))
    role=st.selectbox("New access",["viewer","collector","manager","owner","removed"])
    if st.button("Update member access"):
        ctx["accounts"].set_role(ctx["token"],ctx["business"],selected,role)
        notify("Member access updated.")
st.subheader("Configurable connectors")
st.write("Connect email, WhatsApp, SMS, payments and accounting through your chosen provider gateway. The gateway must implement the documented request and webhook contract.")
st.caption("The server operator must allow the gateway hostname in CONNECTOR_ALLOWED_HOSTS. Saved credentials are encrypted and are never displayed again.")
st.dataframe(ctx["connectors"].list(ctx["token"],ctx["business"]),hide_index=True)
with st.form("connector"):
    kind=st.selectbox("Connector",["email","whatsapp","sms","payments","accounting"])
    endpoint=st.text_input("HTTPS gateway endpoint")
    api_token=st.text_input("API token (leave blank to retain the existing value)",type="password")
    webhook_secret=st.text_input("Webhook signing secret (leave blank to retain the existing value)",type="password")
    enabled=st.checkbox("Enable connector",value=True)
    save=st.form_submit_button("Save connector")
if save:
    ctx["connectors"].save(ctx["token"],ctx["business"],kind,endpoint,api_token,webhook_secret,enabled)
    notify("Connector settings saved. No test message or payment request was sent.")
with st.expander("Import from accounting"):
    if st.button("Fetch invoices for review"):
        st.session_state.sync_records=ctx["connectors"].accounting_preview(ctx["token"],ctx["business"])
        st.session_state.sync_business=ctx["business"]
    if st.session_state.get("sync_business")==ctx["business"] and st.session_state.get("sync_records"):
        st.dataframe(st.session_state.sync_records,hide_index=True)
        if st.button("Import reviewed accounting invoices"):
            count=ctx["ledger"].import_records(st.session_state.sync_records)
            del st.session_state.sync_records
            notify(f"Imported {count} accounting invoices.")
st.caption("Business identifier for gateway configuration: "+ctx["business"])
