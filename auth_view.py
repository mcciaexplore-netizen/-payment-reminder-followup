"""A focused entry screen with native, accessible Streamlit account forms."""
import streamlit as st
import os

from branding import show_header_logo
from demo_workspace import demo_credentials, login_demo
from workspace_store import WorkspaceError


def render_access(accounts, store):
    with st.container(key="auth_shell"):
        story, access = st.columns([1.18, 1], gap="large", vertical_alignment="top")
        with story, st.container(key="auth_story"):
            show_header_logo()
            st.html('<div class="auth-eyebrow">AUTOMATED INVOICE &amp; PAYMENT REMINDERS</div>')
            st.title("Never miss a payment.")
            st.html('''
                <p class="auth-lead">One workspace for automated invoice tracking, smart WhatsApp/Email reminders, and day-to-day cash flow management.</p>
                <div class="auth-feature-cards">
                    <div class="auth-feature-card">
                        <div class="feature-icon feature-icon-blue">01</div>
                        <div class="feature-body">
                            <strong>Live Invoice Tracking & Aging</strong>
                            <p>Track due dates, overdue buckets (0-30, 31-60, 90+ days), and customer balances in real time.</p>
                        </div>
                    </div>
                    <div class="auth-feature-card">
                        <div class="feature-icon feature-icon-green">02</div>
                        <div class="feature-body">
                            <strong>Smart AI & Scheduled Reminders</strong>
                            <p>Generate polite, professional follow-up messages across email and messaging channels automatically.</p>
                        </div>
                    </div>
                    <div class="auth-feature-card">
                        <div class="feature-icon feature-icon-slate">03</div>
                        <div class="feature-body">
                            <strong>Payment Plans & Audit Trails</strong>
                            <p>Record receipts, manage installment promises, and maintain complete collection audit history.</p>
                        </div>
                    </div>
                </div>
                <div class="auth-org-badge">
                    <span class="auth-org-dot"></span>
                    <span>Mahratta Chamber of Commerce, Industries and Agriculture (MCCIA)</span>
                </div>
            ''')
        with access, st.container(key="auth_panel"):
            if "auth_mode" not in st.session_state:
                st.session_state.auth_mode = "login"

            if st.session_state.auth_mode == "register":
                st.subheader("Create your Account")
                st.caption("Enter your details to create a new workspace account.")
                with st.form("ws_register"):
                    business_name = st.text_input("Business / Company name", placeholder="e.g. Acme Enterprises")
                    email = st.text_input("Email address", placeholder="you@business.com")
                    password = st.text_input("Password (at least 12 characters)", type="password", help="Use at least 12 characters.")
                    confirm_password = st.text_input("Confirm password", type="password")
                    tz = st.text_input("Business timezone", value="Asia/Kolkata")
                    submitted = st.form_submit_button("Create account →", type="primary", width="stretch")
                if submitted:
                    if not business_name.strip():
                        st.error("Please enter your business / company name.")
                    elif not email.strip():
                        st.error("Please enter a valid email address.")
                    elif len(password) < 12:
                        st.error("Password must be at least 12 characters.")
                    elif password != confirm_password:
                        st.error("Passwords do not match. Please re-enter.")
                    else:
                        try:
                            st.session_state.ws_token, st.session_state.ws_business = accounts.register(email, password, business_name, tz)
                            st.rerun()
                        except (WorkspaceError, ValueError) as exc:
                            st.error(str(exc))
                
                with st.container(key="auth_switch_box"):
                    if st.button("Already have an account? Sign in", icon=":material/login:", type="tertiary", width="stretch"):
                        st.session_state.auth_mode = "login"
                        st.rerun()
            else:
                st.subheader("Welcome back")
                st.caption("Sign in to your payment follow-up workspace.")
                with st.form("ws_login"):
                    email = st.text_input("Email", key="ws_login_email", placeholder="you@business.com")
                    password = st.text_input("Password", type="password", key="ws_login_password")
                    submitted = st.form_submit_button("Sign in →", type="primary", width="stretch")
                if submitted:
                    try:
                        st.session_state.ws_token = accounts.login(email, password)
                        st.rerun()
                    except (WorkspaceError, ValueError) as exc:
                        st.error(str(exc))

                with st.container(key="auth_switch_box"):
                    if st.button("New user? Create an account", icon=":material/person_add:", type="tertiary", width="stretch"):
                        st.session_state.auth_mode = "register"
                        st.rerun()
