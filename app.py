"""
app.py  —  Payment Follow-up Agent
────────────────────────────────────────────────────────────────
Run locally : streamlit run app.py
HuggingFace : pushed as a Streamlit Space (auto-deploys)

Two modes detected automatically:
  LOCAL MODE  — SPACE_ID env var is NOT set
                → shows setup wizard if credentials are missing

  DEMO MODE   — SPACE_ID env var IS set (HuggingFace Spaces)
                → uses MCCIA's API keys (set as HF Secrets)
"""

import json
import os
import smtplib
import tempfile
from pathlib import Path

import streamlit as st
from dotenv import set_key
from groq import Groq

# ── Mode detection ────────────────────────────────────────────
# HuggingFace automatically sets SPACE_ID on all Spaces
IS_HF    = bool(os.getenv("SPACE_ID"))
IS_LOCAL = not IS_HF

# ── Load config (after detecting mode) ───────────────────────
from config import (
    GROQ_API_KEY, GROQ_MODEL, BUSINESS_NAME,
    DRY_RUN, LOG_PATH,
)
from tools import filter_overdue, log_reminder_sent, read_invoices, send_email

# ─────────────────────────────────────────────────────────────
# Page setup
# ─────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Payment Follow-up Agent — MCCIA AI Studio",
    page_icon="📬",
    layout="centered",
)

# ─────────────────────────────────────────────────────────────
# Session state
# ─────────────────────────────────────────────────────────────

def _init():
    defaults = {
        "step":            "setup" if IS_LOCAL and not _is_configured() else "upload",
        "setup_substep":   "groq",     # groq → gmail → name → done
        "overdue":         [],
        "drafts":          [],
        "results":         [],
        "dry_run":         DRY_RUN,
        # Runtime credentials (used only if not in .env)
        "rt_groq_key":     GROQ_API_KEY,
        "rt_gmail_user":   os.getenv("GMAIL_USER", ""),
        "rt_gmail_pass":   os.getenv("GMAIL_APP_PASSWORD", ""),
        "rt_biz_name":     BUSINESS_NAME,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

def _is_configured() -> bool:
    """True if all required env vars are set (for local mode)."""
    return all([
        os.getenv("GROQ_API_KEY"),
        os.getenv("GMAIL_USER"),
        os.getenv("GMAIL_APP_PASSWORD"),
    ])

def _active_key()      -> str:  return st.session_state.rt_groq_key   or GROQ_API_KEY
def _active_gmail()    -> str:  return st.session_state.rt_gmail_user  or os.getenv("GMAIL_USER","")
def _active_gmail_pw() -> str:  return st.session_state.rt_gmail_pass  or os.getenv("GMAIL_APP_PASSWORD","")
def _active_biz()      -> str:  return st.session_state.rt_biz_name    or BUSINESS_NAME

_init()

# ─────────────────────────────────────────────────────────────
# Credential verifiers
# ─────────────────────────────────────────────────────────────

def verify_groq(api_key: str) -> tuple[bool, str]:
    try:
        c = Groq(api_key=api_key)
        c.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": "Say OK"}],
            max_tokens=5,
        )
        return True, "✅ Connected to Groq successfully."
    except Exception as exc:
        return False, f"❌ {exc}"


def verify_gmail(user: str, password: str) -> tuple[bool, str]:
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as smtp:
            smtp.login(user, password)
        return True, "✅ Gmail connected successfully."
    except smtplib.SMTPAuthenticationError:
        return False, "❌ Wrong email or App Password. Check the steps below."
    except Exception as exc:
        return False, f"❌ {exc}"


# ─────────────────────────────────────────────────────────────
# Email drafter (Groq)
# ─────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def _draft_email(invoice_json: str, biz_name: str, groq_key: str) -> tuple[str, str]:
    inv    = json.loads(invoice_json)
    client = Groq(api_key=groq_key)

    prompt = f"""Draft a professional payment reminder email.

Invoice:
- Client    : {inv.get('client_name')}
- Invoice No: {inv.get('invoice_no')}
- Amount    : ₹{inv.get('amount')}
- Overdue   : {inv.get('days_overdue')} days
- Notes     : {inv.get('notes') or 'None'}

Return ONLY a valid JSON with keys "subject" and "body".
Body rules: plain text, no markdown, open "Dear [first name],",
mention invoice no + amount, request payment in 3 working days,
close "Warm regards,\\n{biz_name}". Under 150 words.
Return only the JSON."""

    try:
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600,
            temperature=0.2,
        )
        raw  = resp.choices[0].message.content.strip()
        raw  = raw.replace("```json","").replace("```","").strip()
        data = json.loads(raw)
        return data.get("subject","Payment Reminder"), data.get("body","")
    except Exception:
        first = inv.get("client_name","Sir/Madam").split()[0]
        subj  = f"Payment Reminder: {inv.get('invoice_no')} — ₹{inv.get('amount')} Overdue"
        body  = (
            f"Dear {first},\n\n"
            f"This is a reminder that Invoice {inv.get('invoice_no')} "
            f"for ₹{inv.get('amount')} is {inv.get('days_overdue')} days past due.\n\n"
            f"Kindly arrange payment within 3 working days or contact us for queries.\n\n"
            f"Warm regards,\n{biz_name}"
        )
        return subj, body


def _load_overdue(file_obj, days: int) -> list[dict]:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        tmp.write(file_obj.getvalue())
        tmp_path = tmp.name
    raw = json.loads(read_invoices(tmp_path))
    os.unlink(tmp_path)
    if raw.get("status") == "error":
        st.error(f"Could not read file: {raw['message']}")
        return []
    filtered = json.loads(filter_overdue(json.dumps(raw), days))
    if filtered.get("status") == "error":
        st.error(f"Filter error: {filtered['message']}")
        return []
    return filtered.get("overdue_invoices", [])


# ─────────────────────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────────────────────

with st.sidebar:
    st.image(
        "https://mcciapune.com/static/assets/images/logos/logo-mccia-white-blue-new.png",
        width=160,
    )
    st.markdown("**Payment Follow-up Agent**")
    st.caption("by MCCIA AI Studio, Pune")
    st.divider()

    if IS_HF:
        st.markdown("**Mode:** 🌐 HuggingFace Demo")
        st.markdown("Want your own copy?")
        st.markdown("[Contact MCCIA AI Studio →](https://mcciapune.com)")

    else:
        # Local mode — show dry run toggle + credential status
        st.markdown("**Mode:** 💻 Local")
        st.divider()
        st.session_state.dry_run = st.toggle(
            "Dry run mode",
            value=st.session_state.dry_run,
            help="ON = preview only, nothing sent. OFF = emails sent.",
        )
        if st.session_state.dry_run:
            st.info("Dry run ON — emails will not be sent.")
        else:
            st.warning("Dry run OFF — emails WILL be sent.")

        st.divider()
        groq_ok  = bool(_active_key())
        gmail_ok = bool(_active_gmail() and _active_gmail_pw())
        st.markdown(f"{'✅' if groq_ok  else '❌'}  Groq API key")
        st.markdown(f"{'✅' if gmail_ok else '❌'}  Gmail credentials")
        if st.session_state.step not in ("setup",) and (groq_ok or gmail_ok):
            if st.button("⚙️ Edit credentials"):
                st.session_state.step = "setup"
                st.session_state.setup_substep = "groq"
                st.rerun()

# ─────────────────────────────────────────────────────────────
# Page title
# ─────────────────────────────────────────────────────────────

st.title("📬 Payment Follow-up Agent")
st.caption("Upload invoices → AI drafts personalised reminders → you approve → send")
st.divider()


# ═════════════════════════════════════════════════════════════
# SETUP WIZARD  (Local mode only)
# ═════════════════════════════════════════════════════════════

def page_setup():
    substep = st.session_state.setup_substep

    # ── Progress indicator ────────────────────────────────────
    steps       = ["Groq API", "Gmail", "Business", "Done"]
    step_idx    = {"groq": 0, "gmail": 1, "name": 2, "done": 3}
    current_idx = step_idx.get(substep, 0)
    cols        = st.columns(len(steps))
    for i, (col, label) in enumerate(zip(cols, steps)):
        icon = "✅" if i < current_idx else ("🔵" if i == current_idx else "⚪")
        col.markdown(f"**{icon} {label}**")

    st.divider()

    # ── Substep: Groq API key ─────────────────────────────────
    if substep == "groq":
        st.markdown("### Step 1 — Connect your AI (Groq)")
        st.markdown(
            "This agent uses **Groq** — a free AI service — to write your "
            "payment reminder emails. You'll need a free API key."
        )
        with st.expander("📋 How to get a free Groq API key (30 seconds)", expanded=True):
            st.markdown(
                "1. Open [console.groq.com](https://console.groq.com) in a new tab\n"
                "2. Sign up with your email — no credit card needed\n"
                "3. Click **API Keys** in the left menu\n"
                "4. Click **Create API Key** → copy the key that starts with `gsk_`\n"
                "5. Paste it below"
            )

        key = st.text_input(
            "Groq API key",
            type="password",
            placeholder="gsk_...",
            value=st.session_state.rt_groq_key,
        )

        if st.button("Verify & Continue →", type="primary", disabled=not key):
            with st.spinner("Connecting to Groq..."):
                ok, msg = verify_groq(key)
            if ok:
                st.session_state.rt_groq_key = key
                # Save to .env if local
                env_path = Path(".env")
                if env_path.exists():
                    set_key(".env", "GROQ_API_KEY", key)
                st.success(msg)
                st.session_state.setup_substep = "gmail"
                st.rerun()
            else:
                st.error(msg)
                st.markdown("Double-check the key was copied in full and starts with `gsk_`.")

    # ── Substep: Gmail ────────────────────────────────────────
    elif substep == "gmail":
        st.markdown("### Step 2 — Connect your Gmail")
        st.markdown(
            "The agent sends reminders from your Gmail account. "
            "You need an **App Password** — your regular Gmail password will not work."
        )
        with st.expander("📋 How to create a Gmail App Password (2 minutes)", expanded=True):
            st.markdown(
                "1. Go to [myaccount.google.com](https://myaccount.google.com)\n"
                "2. Click **Security** in the left menu\n"
                "3. Under 'How you sign in to Google', enable **2-Step Verification** if it's off\n"
                "4. Search **App Passwords** in the search bar at the top\n"
                "5. Click it → name it `payment-agent` → click **Create**\n"
                "6. Google shows a 16-character password — copy it exactly (spaces included)\n"
                "7. Paste it in the field below"
            )
            st.info(
                "Your Gmail password is stored only in your local `.env` file "
                "and is never sent to MCCIA or any third party."
            )

        gmail = st.text_input(
            "Your Gmail address",
            placeholder="yourname@gmail.com",
            value=st.session_state.rt_gmail_user,
        )
        pw = st.text_input(
            "Gmail App Password (16 characters)",
            type="password",
            placeholder="xxxx xxxx xxxx xxxx",
            value=st.session_state.rt_gmail_pass,
        )

        col_back, col_next = st.columns([1, 2])
        with col_back:
            if st.button("← Back"):
                st.session_state.setup_substep = "groq"
                st.rerun()
        with col_next:
            if st.button("Verify & Continue →", type="primary", disabled=not (gmail and pw)):
                with st.spinner("Testing Gmail connection..."):
                    ok, msg = verify_gmail(gmail, pw)
                if ok:
                    st.session_state.rt_gmail_user = gmail
                    st.session_state.rt_gmail_pass = pw
                    env_path = Path(".env")
                    if env_path.exists():
                        set_key(".env", "GMAIL_USER",         gmail)
                        set_key(".env", "GMAIL_APP_PASSWORD", pw)
                    st.success(msg)
                    st.session_state.setup_substep = "name"
                    st.rerun()
                else:
                    st.error(msg)

    # ── Substep: Business name ────────────────────────────────
    elif substep == "name":
        st.markdown("### Step 3 — Your business name")
        st.markdown(
            "This appears in the sign-off of every reminder email "
            "(e.g. 'Warm regards, **Sharma Electronics**')."
        )

        name = st.text_input(
            "Business / company name",
            placeholder="e.g. Sharma Electronics Pvt Ltd",
            value=_active_biz(),
        )

        col_back, col_next = st.columns([1, 2])
        with col_back:
            if st.button("← Back"):
                st.session_state.setup_substep = "gmail"
                st.rerun()
        with col_next:
            if st.button("Save & Start →", type="primary", disabled=not name.strip()):
                st.session_state.rt_biz_name = name.strip()
                env_path = Path(".env")
                if env_path.exists():
                    set_key(".env", "BUSINESS_NAME", name.strip())
                st.session_state.setup_substep = "done"
                st.rerun()

    # ── Substep: Done ─────────────────────────────────────────
    elif substep == "done":
        st.success("✅ Setup complete! You're ready to send payment reminders.")
        st.markdown(
            f"- **Groq AI** — connected\n"
            f"- **Gmail** — {st.session_state.rt_gmail_user}\n"
            f"- **Business** — {_active_biz()}"
        )
        st.caption("Your credentials are saved in the `.env` file in your project folder.")
        if st.button("Go to the app →", type="primary", use_container_width=True):
            st.session_state.step = "upload"
            st.rerun()


# ═════════════════════════════════════════════════════════════
# STEP 1  ·  Upload
# ═════════════════════════════════════════════════════════════

def page_upload():
    if IS_HF:
        st.info(
            "🌐 **Demo mode** — emails will be sent from MCCIA's account as a demo. "
            "In your own copy, they'll come from your Gmail."
        )

    st.markdown("### Step 1 — Upload your invoices file")
    st.markdown(
        "Your Excel file needs these columns: "
        "**Invoice No, Client Name, Email, Amount, Due Date, Status**"
    )

    uploaded = st.file_uploader(
        "Drag and drop your invoices Excel here",
        type=["xlsx"],
        label_visibility="collapsed",
    )

    days = st.slider(
        "Send reminders for invoices overdue by at least:",
        min_value=1, max_value=60, value=7, step=1, format="%d days",
    )

    st.markdown("")

    if uploaded:
        if st.button("🔍 Find overdue invoices", type="primary", use_container_width=True):
            with st.spinner("Reading file and drafting emails with AI..."):
                overdue = _load_overdue(uploaded, days)
                if not overdue:
                    st.success("✅ No overdue invoices found — all payments are on track!")
                    st.stop()

                drafts   = []
                progress = st.progress(0, text="Generating email drafts...")
                groq_key = _active_key()

                for idx, inv in enumerate(overdue):
                    subj, body = _draft_email(json.dumps(inv), _active_biz(), groq_key)
                    drafts.append({"invoice": inv, "subject": subj, "body": body})
                    progress.progress(
                        (idx + 1) / len(overdue),
                        text=f"Drafting {idx + 1} of {len(overdue)}...",
                    )

                st.session_state.overdue = overdue
                st.session_state.drafts  = drafts
                st.session_state.step    = "review"
                st.rerun()
    else:
        st.info("Upload an Excel file above to get started.")

    if IS_HF:
        with st.expander("📥 Don't have an Excel file? Download a sample"):
            import pandas as pd
            from datetime import date, timedelta
            t = date.today()
            sample = pd.DataFrame({
                "Invoice No":   ["INV-001", "INV-002", "INV-003"],
                "Client Name":  ["Sharma Electronics", "Rathi Textiles", "Kiran Auto"],
                "Email":        ["accounts@sharma.com", "finance@rathi.in", "kiran@auto.co.in"],
                "Amount":       [45000, 12500, 78000],
                "Invoice Date": [(t - timedelta(50)).isoformat()] * 3,
                "Due Date": [
                    (t - timedelta(30)).isoformat(),
                    (t - timedelta(15)).isoformat(),
                    (t - timedelta(8)).isoformat(),
                ],
                "Status": ["Unpaid", "Unpaid", "Unpaid"],
                "Notes": ["", "Partial ₹2000 received", ""],
            })
            import io
            buf = io.BytesIO()
            sample.to_excel(buf, index=False)
            st.download_button(
                "📥 Download sample invoices.xlsx",
                data=buf.getvalue(),
                file_name="sample_invoices.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


# ═════════════════════════════════════════════════════════════
# STEP 2  ·  Review
# ═════════════════════════════════════════════════════════════

def page_review():
    drafts = st.session_state.drafts

    total_amount = sum(
        float(str(d["invoice"].get("amount", 0)).replace(",", ""))
        for d in drafts
        if str(d["invoice"].get("amount", "")).replace(",", "").replace(".", "").isdigit()
    )

    c1, c2 = st.columns(2)
    c1.metric("Overdue invoices",     len(drafts))
    c2.metric("Total overdue amount", f"₹{total_amount:,.0f}")

    st.markdown("### Step 2 — Review and approve each email")
    st.markdown(
        "Edit the subject or body if needed. "
        "Toggle **Send** off to skip an invoice."
    )
    st.divider()

    for i, draft in enumerate(drafts):
        inv = draft["invoice"]
        if f"approved_{i}" not in st.session_state:
            st.session_state[f"approved_{i}"] = True

        with st.container(border=True):
            h_col, t_col = st.columns([3, 1])
            with h_col:
                st.markdown(f"**{inv.get('client_name','Unknown')}**")
                st.caption(
                    f"Invoice {inv.get('invoice_no','—')}  ·  "
                    f"₹{inv.get('amount','—')}  ·  "
                    f"{inv.get('days_overdue', 0)} days overdue"
                )
                email_addr = inv.get("email","")
                if email_addr:
                    st.caption(f"📧 {email_addr}")
                else:
                    st.warning("⚠️ No email address for this client — will be skipped.")
            with t_col:
                approved = st.toggle("Send", value=st.session_state[f"approved_{i}"], key=f"toggle_{i}")
                st.session_state[f"approved_{i}"] = approved

            if approved:
                if not email_addr:
                    st.session_state[f"approved_{i}"] = False
                else:
                    st.text_input("Subject", value=draft["subject"], key=f"subject_{i}")
                    st.text_area("Email body", value=draft["body"], key=f"body_{i}", height=180)
            else:
                st.caption("*Skipped — toggle Send on to include.*")

    st.divider()

    approved_count = sum(
        1 for i in range(len(drafts))
        if st.session_state.get(f"approved_{i}", True)
        and drafts[i]["invoice"].get("email")
    )

    col_back, col_send = st.columns([1, 2])
    with col_back:
        if st.button("← Back", use_container_width=True):
            for i in range(len(drafts)):
                st.session_state.pop(f"approved_{i}", None)
                st.session_state.pop(f"subject_{i}",  None)
                st.session_state.pop(f"body_{i}",     None)
            st.session_state.step = "upload"
            st.rerun()

    with col_send:
        lbl = f"📤 Send {approved_count} email{'s' if approved_count != 1 else ''}"
        if st.button(lbl, type="primary", use_container_width=True, disabled=approved_count == 0):
            results  = []
            to_send  = [
                (i, d) for i, d in enumerate(drafts)
                if st.session_state.get(f"approved_{i}", True)
                and d["invoice"].get("email")
            ]
            progress = st.progress(0, text="Sending emails...")
            import tools as _tools
            _tools.DRY_RUN        = st.session_state.dry_run
            _tools.GMAIL_USER     = _active_gmail()
            _tools.GMAIL_APP_PASSWORD = _active_gmail_pw()

            for j, (i, draft) in enumerate(to_send):
                inv     = draft["invoice"]
                subject = st.session_state.get(f"subject_{i}", draft["subject"])
                body    = st.session_state.get(f"body_{i}",    draft["body"])
                email   = inv.get("email","")

                res = json.loads(send_email(email, subject, body))
                if res.get("status") in ("ok","dry_run"):
                    log_reminder_sent(
                        inv.get("invoice_no",""), inv.get("client_name",""),
                        email, str(inv.get("amount","")),
                        int(inv.get("days_overdue",0)), body,
                    )
                    status = "dry_run" if st.session_state.dry_run else "sent"
                else:
                    status = f"error: {res.get('message','Unknown')}"

                results.append({
                    "name": inv.get("client_name"), "invoice_no": inv.get("invoice_no"),
                    "amount": inv.get("amount"), "status": status,
                })
                progress.progress((j+1)/len(to_send), text=f"Sending {j+1}/{len(to_send)}...")

            for i, draft in enumerate(drafts):
                if not st.session_state.get(f"approved_{i}", True):
                    inv = draft["invoice"]
                    results.append({
                        "name": inv.get("client_name"), "invoice_no": inv.get("invoice_no"),
                        "amount": inv.get("amount"), "status": "skipped",
                    })

            st.session_state.results = results
            st.session_state.step    = "done"
            st.rerun()


# ═════════════════════════════════════════════════════════════
# STEP 3  ·  Done
# ═════════════════════════════════════════════════════════════

def page_done():
    results = st.session_state.results
    sent    = [r for r in results if r["status"] in ("sent","dry_run")]
    skipped = [r for r in results if r["status"] == "skipped"]
    failed  = [r for r in results if r["status"].startswith("error")]

    if st.session_state.dry_run:
        st.success(f"✅ Dry run complete — {len(sent)} previewed, {len(skipped)} skipped.")
        st.info("Switch off Dry run mode in the sidebar and run again to actually send.")
    else:
        st.success(f"✅ Done — {len(sent)} sent, {len(skipped)} skipped.")
    if failed:
        st.error(f"{len(failed)} email(s) failed.")

    st.divider()
    st.markdown("### Summary")
    icons  = {"sent":"✅","dry_run":"🟡","skipped":"⏭"}
    labels = {"sent":"Sent","dry_run":"Previewed (dry run)","skipped":"Skipped"}
    for r in results:
        icon  = icons.get(r["status"],"❌")
        label = labels.get(r["status"], r["status"])
        st.markdown(f"{icon} &nbsp; **{r['name']}** — {r['invoice_no']} · ₹{r['amount']} · {label}")

    log_path = Path(LOG_PATH)
    if log_path.exists() and log_path.stat().st_size > 0:
        st.divider()
        with open(log_path,"rb") as fh:
            st.download_button(
                "📥 Download reminder log (CSV)", data=fh.read(),
                file_name="reminders_log.csv", mime="text/csv",
                use_container_width=True,
            )

    if IS_HF:
        st.divider()
        st.markdown("### Want your own copy of this agent?")
        st.markdown(
            "This demo runs with MCCIA's API keys. Get your own copy — "
            "with your branding and your own Gmail."
        )
        c1, c2 = st.columns(2)
        c1.markdown("📧 ismail.fellow@mcciapune.com")
        c2.markdown("📞 +91 88558 85290")

    st.markdown("")
    if st.button("🔄 Start over", use_container_width=True):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()


# ─────────────────────────────────────────────────────────────
# Router — decide which page to show
# ─────────────────────────────────────────────────────────────

step = st.session_state.step

if   step == "setup"  : page_setup()
elif step == "upload" : page_upload()
elif step == "review" : page_review()
elif step == "done"   : page_done()
