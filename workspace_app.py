"""Authenticated MSME workspace entry point."""
import sqlite3
import os
from datetime import datetime
from zoneinfo import ZoneInfo
import streamlit as st

from accounts import Accounts
from branding import configure_page
from auth_view import render_access
from demo_workspace import SEED_ACTION
from ui_design import page_heading
from workspace_store import WorkspaceError,WorkspaceStore
from workspace_ui import database_path
from workspace_storage import StorageConfigurationError


def main():
    configure_page()
    try:
        store=WorkspaceStore(database_path())
    except StorageConfigurationError as exc:
        st.title("Payment follow-up")
        st.error("Workspace storage setup is incomplete.")
        st.info(str(exc))
        return
    except (sqlite3.Error,OSError):
        st.title("Payment follow-up")
        if os.getenv("WORKSPACE_DATABASE_URL") or os.getenv("DATABASE_URL") or os.getenv("TURSO_DATABASE_URL"):
            st.error("The hosted workspace database could not be reached. Check the database connection settings and service availability.")
        else:
            st.error("The private workspace database could not be opened. Check the configured data folder and the app's permission to read and write it.")
        return
    accounts=Accounts(store)
    st.session_state.setdefault("ws_token","")
    st.session_state.setdefault("ws_business","")
    companies=accounts.businesses(st.session_state.ws_token)
    if not companies:
        # Replace the previous workspace navigation when the session ends.
        access=st.Page(lambda:render_access(accounts,store),title="Sign in",url_path="sign-in",default=True)
        st.navigation([access],position="hidden").run()
        return
    # Native desktop locking also removes Streamlit's collapse keyboard behavior.
    st.set_page_config(initial_sidebar_state="locked")
    ids=[b["id"] for b in companies]
    previous=st.session_state.ws_business
    sidebar=st.sidebar.container(key="workspace_sidebar",gap="small")
    selected=sidebar.selectbox("Business",ids,index=ids.index(previous) if previous in ids else 0,
        format_func=lambda value:next(b["name"] for b in companies if b["id"]==value))
    if previous!=selected:
        # Clear reviewed content and temporary data when switching tenants.
        for key in list(st.session_state):
            if key.startswith(("draft_","sync_","portal_","import_")):
                del st.session_state[key]
        st.session_state.ws_business=selected
    company=next(b for b in companies if b["id"]==selected)
    sidebar.caption(f"{company['role'].capitalize()} · {company['timezone']}")
    with store.transaction() as db:
        sample_data=db.execute("SELECT 1 FROM ws_audit WHERE business_id=? AND action=?",(selected,SEED_ACTION)).fetchone()
    if sample_data:
        sidebar.caption("Demo workspace · Sample data")
    sidebar.caption("Live messaging enabled" if os.getenv("WORKSPACE_LIVE_ENABLED","").lower()=="true" else "Preview mode · No live messages")
    if sidebar.button("Sign out",icon=":material/logout:",width="stretch"):
        accounts.logout(st.session_state.ws_token)
        st.session_state.clear()
        st.rerun()
    pages=[
        st.Page("app_pages/dashboard.py",title="Overview",icon=":material/dashboard:"),
        st.Page("app_pages/invoice_book.py",title="Invoices & customers",icon=":material/receipt_long:"),
        st.Page("app_pages/payments.py",title="Payments & plans",icon=":material/payments:"),
        st.Page("app_pages/reminder_queue.py",title="Reminders",icon=":material/notifications:"),
        st.Page("app_pages/policies.py",title="Schedules & templates",icon=":material/event_repeat:"),
        st.Page("app_pages/reports.py",title="Reports & history",icon=":material/bar_chart:"),
        st.Page("app_pages/settings.py",title="Business settings",icon=":material/settings:"),
    ]
    if company["role"]=="owner":
        pages.append(st.Page("app_pages/team.py",title="Team & integrations",icon=":material/group:"))
    page=st.navigation({"Collections":pages[:4],"Manage":pages[4:]},expanded=True)
    page_heading(page.title,company["name"],datetime.now(ZoneInfo(company["timezone"])).date())
    if st.session_state.get("ws_notice"):
        st.success(st.session_state.pop("ws_notice"))
    try:
        page.run()
    except (WorkspaceError,ValueError) as exc:
        st.error(str(exc))
    except sqlite3.Error:
        st.error("The workspace database could not complete this operation. Check storage and try again; inspect reminder history before resending.")


if __name__=="__main__":
    main()
