"""Streamlit interface for the shared, approval-controlled reminder workflow."""
import sqlite3
import smtplib
import ssl
import tempfile
import uuid
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import streamlit as st

import config
from branding import configure_page, show_header_logo
from config import SendSettings, save_local_settings
from drafting import draft_email
from invoices import InvoiceValidationError, email_address, read_invoice_file
from reminders import ReminderError, ReminderService, ReminderStore
from setup_sample import sample_bytes

configure_page(layout="centered")


def reset_batch():
    for key in list(st.session_state):
        if key.startswith("review_") or key == "invoice_upload":
            del st.session_state[key]
    st.session_state.update(step="upload", drafts=[], results=[], batch_id=uuid.uuid4().hex)


def make_service():
    if "store" not in st.session_state:
        if config.IS_DEMO:
            # One private temporary database per demo session; no host history.
            tempdir = tempfile.TemporaryDirectory(prefix="payment-demo-")
            st.session_state.demo_directory = tempdir
            path = Path(tempdir.name) / "reminders.sqlite3"
        else:
            path = config.DATABASE_PATH
        st.session_state.store = ReminderStore(path)
    return ReminderService(st.session_state.store, SendSettings(
        business_id=st.session_state.business_id,
        sender=st.session_state.gmail_user, password=st.session_state.gmail_password,
        dry_run=st.session_state.dry_run, preview_only=config.IS_DEMO,
        days_threshold=st.session_state.days_threshold))


saved_settings = config.read_local_settings()
defaults = dict(step="upload", drafts=[], results=[], batch_id=uuid.uuid4().hex,
                business_id=uuid.uuid4().hex if config.IS_DEMO else config.BUSINESS_ID,
                business_name="Demo business" if config.IS_DEMO else saved_settings["BUSINESS_NAME"],
                gmail_user=saved_settings["GMAIL_USER"],
                gmail_password=saved_settings["GMAIL_APP_PASSWORD"],
                groq_key=saved_settings["GROQ_API_KEY"],
                dry_run=True if config.IS_DEMO else config.DRY_RUN,
                days_threshold=config.DEFAULT_OVERDUE_DAYS)
for key, value in defaults.items():
    st.session_state.setdefault(key, value)

show_header_logo()
st.title("Payment follow-up")
st.caption("Import invoices, review the outstanding amounts, and approve each reminder.")

with st.sidebar:
    st.subheader("Payment follow-up")
    st.caption("MCCIA AI Studio")
    if config.IS_DEMO:
        st.info("Preview-only demo. No emails are sent. This session has its own temporary history.")
    else:
        st.toggle("Dry run mode", key="dry_run")
        if st.session_state.dry_run:
            st.info("Preview only. No emails will be sent.")
        else:
            st.warning("Live mode. Approved emails will be sent through Gmail.")
        st.caption("Local business workspace. Keep this app on your trusted computer.")
        if st.button("Business and email settings", disabled=st.session_state.step == "review"):
            st.session_state.step = "setup"
            st.rerun()

try:
    service = make_service()
except (ValueError, OSError, sqlite3.Error) as exc:
    st.error(f"Could not open the reminder workspace: {exc}")
    st.stop()

if st.session_state.step == "setup" and not config.IS_DEMO:
    st.subheader("Business and email settings")
    st.caption("AI is optional. You can preview reminders with the built-in template before connecting Gmail.")
    with st.form("settings_form"):
        business = st.text_input("Business name", value=st.session_state.business_name)
        sender = st.text_input("Gmail address", value=st.session_state.gmail_user)
        password = st.text_input("Gmail App Password", value=st.session_state.gmail_password, type="password")
        api_key = st.text_input("Groq API key (optional)", value=st.session_state.groq_key, type="password")
        persist = st.checkbox("Save settings on this computer", value=True,
                              help="Stored in the local .env file. Protect access to this computer.")
        save = st.form_submit_button("Save settings", type="primary")
    if save:
        try:
            if not business.strip() or len(business.strip()) > 200:
                raise ValueError("Business name is required and must be at most 200 characters.")
            if sender:
                sender = email_address(sender)
            if bool(sender) != bool(password.strip()):
                raise ValueError("Enter both Gmail address and App Password, or leave both blank for previews.")
            values = {"BUSINESS_NAME": business.strip(), "GMAIL_USER": sender,
                      "GMAIL_APP_PASSWORD": password.replace(" ", ""), "GROQ_API_KEY": api_key.strip()}
            if persist:
                save_local_settings(values)
            st.session_state.update(business_name=values["BUSINESS_NAME"], gmail_user=sender,
                                    gmail_password=values["GMAIL_APP_PASSWORD"], groq_key=values["GROQ_API_KEY"])
            st.success("Settings saved on this computer." if persist else "Settings saved for this browser session.")
        except (OSError, ValueError) as exc:
            st.error(str(exc))
    if st.button("Test Gmail connection", disabled=not st.session_state.gmail_user):
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10, context=ssl.create_default_context()) as smtp:
                smtp.login(st.session_state.gmail_user, st.session_state.gmail_password)
            st.success("Gmail connection verified. No email was sent.")
        except Exception:
            st.error("Gmail connection failed. Check the address, App Password, and network connection.")
    if st.button("Continue to invoices", type="primary"):
        st.session_state.step = "upload"
        st.rerun()

elif st.session_state.step == "upload":
    st.subheader("1. Import invoices")
    st.write("Required columns: Invoice No, Client Name, Email, Amount, Due Date, Status.")
    st.caption("Amount is the invoice total. Use Amount Paid for receipts, and Currency if needed (default INR). "
               "Dates: YYYY-MM-DD or DD/MM/YYYY. Disputed and On Hold invoices are paused. Maximum 5 MB / 5,000 rows.")
    st.download_button("Download sample invoices", sample_bytes(), file_name="sample_invoices.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    uploaded = st.file_uploader("Invoice file", type=["xlsx", "csv"], key="invoice_upload")
    days = st.slider("Minimum days overdue", 1, 365, st.session_state.days_threshold)
    limit = st.number_input("Maximum reminders in this batch", min_value=1, max_value=100, value=25)
    use_ai = False
    if not config.IS_DEMO and st.session_state.groq_key:
        use_ai = st.checkbox("Use AI for the reminder wording", value=False)
        st.caption("Only a generic writing instruction is sent to the AI provider; invoice details stay here.")
    if st.button("Find overdue invoices", type="primary", disabled=uploaded is None):
        try:
            st.session_state.days_threshold = days
            service = make_service()
            rows = read_invoice_file(uploaded)
            # No invoice is stored until every imported row has passed validation.
            valid = service.import_invoices(rows)
            overdue, suppressed = service.select_overdue(valid)
            if suppressed:
                st.info(f"{suppressed} overdue invoices are excluded because they have a recent or unresolved reminder.")
            if not overdue:
                st.info("No eligible overdue invoices in this file. Paid, paused, and not-yet-due invoices are excluded.")
            else:
                st.session_state.eligible_count = len(overdue)
                batch = overdue[:int(limit)]
                drafts = []
                with st.spinner("Preparing reminders..."):
                    for invoice in batch:
                        draft = draft_email(invoice, st.session_state.business_name,
                                            st.session_state.groq_key if use_ai else "", config.GROQ_MODEL)
                        drafts.append({"invoice": invoice, "subject": draft.subject, "body": draft.body, "source": draft.source})
                st.session_state.update(drafts=drafts, step="review", batch_id=uuid.uuid4().hex)
                st.rerun()
        except InvoiceValidationError as exc:
            st.error("Import stopped. Correct these issues and upload the file again.")
            for error in exc.errors[:50]:
                st.write(error)
            if len(exc.errors) > 50:
                st.write(f"{len(exc.errors)-50} additional errors found.")
        except (ValueError, OSError, sqlite3.Error) as exc:
            st.error(f"Could not prepare reminders: {exc}")

elif st.session_state.step == "review":
    st.subheader("2. Review and approve")
    drafts = st.session_state.drafts
    st.write(f"Reviewing {len(drafts)} of {st.session_state.get('eligible_count', len(drafts))} eligible overdue invoices.")
    totals = defaultdict(Decimal)
    for draft in drafts:
        totals[draft["invoice"]["currency"]] += Decimal(draft["invoice"]["outstanding_amount"])
    for currency, total in totals.items():
        st.metric(f"Outstanding in this batch ({currency})", f"{total:,.2f}")
    st.info("Select only the messages you have reviewed. Editing a message requires a fresh approval when you submit.")
    chosen = []
    with st.form("review_form"):
        for i, draft in enumerate(drafts):
            inv = draft["invoice"]
            key = f"review_{st.session_state.batch_id}_{i}"
            with st.container(border=True):
                st.write(f"{inv['client_name']} | {inv['invoice_no']}")
                st.caption(f"{inv['email']} | {inv['currency']} {inv['outstanding_amount']} outstanding | {inv['days_overdue']} days overdue")
                st.caption(f"Draft: {draft['source']}")
                subject = st.text_input("Subject", value=draft["subject"], key=f"{key}_subject")
                body = st.text_area("Email body", value=draft["body"], height=180, key=f"{key}_body")
                selected = st.checkbox("I approve this recipient and message", key=f"{key}_approved", value=False)
                if selected:
                    chosen.append((inv, subject, body))
        preview = service.mode == "dry_run"
        submit = st.form_submit_button("Approve selected and preview" if preview else "Approve selected and send", type="primary")
    if submit:
        if not chosen:
            st.warning("Select at least one reviewed message to continue.")
        else:
            results = []
            for inv, subject, body in chosen:
                try:
                    token = service.approve(inv, subject, body)
                    result = service.send_approved(token)
                    results.append({"Invoice": inv["invoice_no"], "Status": result.status, "Details": result.message})
                except (ReminderError, ValueError, OSError, sqlite3.Error) as exc:
                    results.append({"Invoice": inv["invoice_no"], "Status": "blocked", "Details": str(exc)})
            st.session_state.update(results=results, step="done")
            st.rerun()
    st.button("Back to import", on_click=reset_batch)

elif st.session_state.step == "done":
    st.subheader("3. Batch results")
    results = st.session_state.results
    if any(r["Status"] in {"failed", "unknown", "blocked"} for r in results):
        st.warning("Some reminders need attention. Check the results before retrying.")
    else:
        st.success("The selected reminders have been processed.")
    st.dataframe(results, hide_index=True)
    st.caption("Submitted means Gmail accepted the email. Delivery and payment are not confirmed by this app.")
    st.button("Start another batch", type="primary", on_click=reset_batch)

try:
    history = service.history()
    if history:
        st.download_button("Download reminder history (CSV)", data=service.history_csv(),
                           file_name="reminder_history.csv", mime="text/csv")
    uncertain = [row for row in history if row["mode"] == "live" and row["state"] in {"unknown", "claimed"}]
    if uncertain and not config.IS_DEMO:
        with st.expander("Resolve an uncertain email attempt"):
            st.write("Check Gmail Sent mail and any active sender before recording an outcome. This does not send an email.")
            with st.form("reconciliation_form"):
                selected = st.selectbox("Attempt", options=[row["id"] for row in uncertain],
                    format_func=lambda value: next(f"{r['invoice_key']} | {r['attempted_at']}" for r in uncertain if r["id"] == value))
                outcome = st.selectbox("Verified outcome", ["Choose an outcome", "Email was submitted", "Email was not sent"])
                note = st.text_input("How did you verify this?")
                confirmed = st.checkbox("I checked Gmail and confirmed that no sender is still running")
                reconcile = st.form_submit_button("Record verified outcome")
            if reconcile:
                try:
                    if not confirmed or outcome not in {"Email was submitted", "Email was not sent"}:
                        raise ReminderError("Confirm the check and choose a verified outcome.")
                    service.reconcile(selected, "submitted" if outcome == "Email was submitted" else "failed", note)
                    st.success("The outcome has been recorded in the audit history.")
                    st.rerun()
                except (ReminderError, sqlite3.Error) as exc:
                    st.error(str(exc))
except sqlite3.Error:
    st.error("Reminder history could not be read. Check the workspace storage.")
