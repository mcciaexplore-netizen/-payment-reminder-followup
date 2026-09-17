"""Shared Streamlit context. User permissions are always checked in services."""
import os
from pathlib import Path
import streamlit as st

import config
from accounts import Accounts
from business_features import BusinessFeatures
from connectors import Connectors,Vault
from ledger import Ledger
from scheduling import Scheduling,Worker
from workspace_store import WorkspaceError,WorkspaceStore
from workspace_storage import database_location


def database_path():
    return database_location()


def context():
    store=WorkspaceStore(database_path())
    token=st.session_state.get("ws_token","")
    business=st.session_state.get("ws_business","")
    accounts=Accounts(store)
    companies=accounts.businesses(token)
    current=next((b for b in companies if b["id"]==business),None)
    if current is None:
        raise WorkspaceError("Sign in and choose a business.")
    gateway=Connectors(store,Vault(store.key_directory))
    return {"store":store,"token":token,"business":business,"company":current,"accounts":accounts,
            "ledger":Ledger(store,token,business),"features":BusinessFeatures(store,token,business),
            "scheduling":Scheduling(store,token,business),"connectors":gateway,"worker":Worker(store,gateway)}


def require_role(ctx,permission):
    from accounts import PERMISSIONS
    if ctx["company"]["role"] not in PERMISSIONS[permission]:
        st.info("Your role has view-only access to this area.")
        st.stop()


def notify(message):
    st.session_state.ws_notice=message
    st.rerun()


def invoice_picker(rows,label="Invoice",key=None):
    if not rows:
        st.info("Import invoices to get started.")
        st.stop()
    selected=st.selectbox(label,[r["invoice_no"] for r in rows],key=key,
        format_func=lambda n:next(f"{r['invoice_no']} · {r['client_name']} · {r['currency']} {r['outstanding_amount']}" for r in rows if r["invoice_no"]==n))
    return next(r for r in rows if r["invoice_no"]==selected)
