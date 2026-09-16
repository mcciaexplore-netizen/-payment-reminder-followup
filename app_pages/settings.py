import streamlit as st
from workspace_ui import context,require_role,notify

ctx=context()
with st.expander("Change your password"):
    with st.form("password"):
        old=st.text_input("Current password",type="password")
        new=st.text_input("New password",type="password")
        changed=st.form_submit_button("Change password and revoke other sessions")
    if changed:
        st.session_state.ws_token=ctx["accounts"].change_password(ctx["token"],old,new)
        notify("Password changed. Other sessions were revoked.")
require_role(ctx,"manage")
brand=ctx["features"].branding()
st.caption("Business defaults apply to built-in reminders. Custom templates can insert the same fields.")
with st.form("branding"):
    name=st.text_input("Display name",value=brand["display_name"] or ctx["company"]["name"])
    reply=st.text_input("Reply contact email",value=brand["reply_email"])
    terms=st.text_area("Payment terms and sign-off details",value=brand["terms"])
    tone=st.selectbox("Default tone",["polite","formal","firm"],index=["polite","formal","firm"].index(brand["tone"]))
    currency=st.text_input("Default currency",value=brand["default_currency"])
    upi=st.text_input("Optional UPI ID",value=brand["upi_id"])
    st.caption("UPI instructions can be included in INR reminders. UPI payments require a verified gateway event or a manually checked receipt to update balances.")
    save=st.form_submit_button("Save business defaults")
if save:
    ctx["features"].save_branding(name,reply,terms,tone,currency.upper(),upi)
    notify("Business defaults saved. Unsent reminders were cancelled so the updated details can be reviewed.")
if ctx["company"]["role"]=="owner":
    st.subheader("Business data export")
    st.caption("Contains invoices, receipts, rules and audit history. Credentials and access tokens are excluded. Full server backup instructions are in the README.")
    st.download_button("Download business archive",ctx["features"].export_business(),file_name="business_archive.zip",mime="application/zip")
    with st.expander("Add another business"):
        with st.form("new_business"):
            name=st.text_input("New business name")
            tz=st.text_input("Timezone",value="Asia/Kolkata")
            create=st.form_submit_button("Create separate business")
        if create:
            st.session_state.ws_business=ctx["accounts"].create_business(ctx["token"],name,tz)
            notify("Separate business created.")
