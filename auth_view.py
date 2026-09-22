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
            st.html('''
                <div class="auth-eyebrow">INVOICES &amp; CASH FLOW FOR EVERY GROWING BUSINESS</div>
                <h1 class="auth-hero-title">Know your cash.<br><span class="highlight-blue">Keep work moving.</span></h1>
                <p class="auth-lead">One workspace for invoices, payment follow-ups, customer ledgers, and day-to-day cash flow.</p>
                <div class="auth-feature-cards">
                    <div class="auth-feature-card">
                        <div class="feature-icon feature-icon-blue">01</div>
                        <div class="feature-body">
                            <strong>Know where you stand</strong>
                            <p>Bring invoices, aging balances, and customer details into one consolidated dashboard.</p>
                        </div>
                    </div>
                    <div class="auth-feature-card">
                        <div class="feature-icon feature-icon-green">02</div>
                        <div class="feature-body">
                            <strong>Make each follow-up count</strong>
                            <p>Generate polite, professional reminders, honor payment promises, and preserve customer trust.</p>
                        </div>
                    </div>
                    <div class="auth-feature-card">
                        <div class="feature-icon feature-icon-slate">03</div>
                        <div class="feature-body">
                            <strong>Keep an immutable record</strong>
                            <p>Track payments, receipts, installment schedules, and complete collection audit trails.</p>
                        </div>
                    </div>
                </div>
                <div class="auth-org-badge">
                    <span class="auth-org-dot"></span>
                    <span>Mahratta Chamber of Commerce, Industries and Agriculture (MCCIA)</span>
                </div>
            ''')
        with access, st.container(key="auth_panel"):
            if not accounts.initialized():

                st.subheader("Set up your business")
                protected = os.getenv("VERCEL")=="1" or bool(os.getenv("WORKSPACE_SETUP_TOKEN")) or store.remote
                if protected and len(os.getenv("WORKSPACE_SETUP_TOKEN","")) < 32:
                    st.info("An administrator needs to configure a private setup code before the first account can be created.")
                    return
                st.caption("Create your first owner account to get started.")
                with st.form("ws_setup"):
                    name=st.text_input("Business name",placeholder="Your business name")
                    email=st.text_input("Owner email",placeholder="you@business.com")
                    password=st.text_input("Password",type="password",help="Use at least 12 characters.")
                    tz=st.text_input("Business timezone",value="Asia/Kolkata")
                    setup_token=st.text_input("Workspace setup code",type="password") if protected else ""
                    submitted=st.form_submit_button("Create business",type="primary",width="stretch")
                if submitted:
                    try:
                        st.session_state.ws_token,st.session_state.ws_business=accounts.bootstrap(email,password,name,tz,setup_token=setup_token)
                        st.rerun()
                    except (WorkspaceError,ValueError) as exc:
                        st.error(str(exc))
                return
            st.subheader("Welcome back")
            st.caption("Sign in to your business workspace.")
            demo=demo_credentials()
            # Demo prefilling happens before the keyed form widgets are rendered.
            if demo:
                with st.container(key="auth_demo"):
                    st.subheader("Take a look around")
                    st.caption("Explore sample invoices and payments. No setup needed.")
                    with st.container(horizontal=True):
                        open_demo=st.button("Open demo workspace",icon=":material/arrow_forward:")
                        fill_demo=st.button("Fill demo login",type="tertiary")
                    if fill_demo:
                        st.session_state.ws_access="Sign in"
                        st.session_state.ws_login_email,st.session_state.ws_login_password=demo
                    if open_demo:
                        try:
                            st.session_state.ws_token,st.session_state.ws_business=login_demo(store)
                            st.rerun()
                        except (WorkspaceError,ValueError):
                            st.error("Demo sign-in is unavailable. Check the demo account and its server configuration.")
            choice=st.segmented_control("Access",["Sign in","Use invitation"],default="Sign in",key="ws_access",label_visibility="collapsed")
            with st.form("ws_login"):
                email=st.text_input("Email",key="ws_login_email",placeholder="you@business.com")
                password=st.text_input("Password",type="password",key="ws_login_password")
                invitation=st.text_input("Invitation code") if choice=="Use invitation" else ""
                submitted=st.form_submit_button("Continue",type="primary",width="stretch")
            if submitted:
                try:
                    if choice=="Use invitation":
                        st.session_state.ws_token,st.session_state.ws_business=accounts.accept_invite(invitation,email,password)
                    else:
                        st.session_state.ws_token=accounts.login(email,password)
                    st.rerun()
                except (WorkspaceError,ValueError) as exc:
                    st.error(str(exc))
            st.caption("Joining a team? Select Use invitation and enter the code shared by your workspace owner.")
