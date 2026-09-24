"""Bank payment email parser, IMAP poller, multi-bank regex engines, automated confirmations & smart installment allocations.

Extracts transaction UTR/reference, amount, and invoice details from bank credit notification emails.
Supports HDFC, ICICI, SBI, Axis, Kotak, Razorpay, Stripe, PhonePe, Paytm, Wise & International wire formats.
Matches payments to open invoices/installments, records receipts, halts future reminders, and dispatches thank-you confirmations.
"""
import imaplib
import email
import re
import secrets
from email.header import decode_header
from datetime import datetime, date, timezone
from decimal import Decimal
import json

from workspace_store import WorkspaceError, business_today, encode, utcnow
from ledger import record_receipt, get_invoice, minor, amount


def parse_bank_email(subject, body):
    """Multi-Bank Alert Format Intelligence: Extracts amount, UTR/reference, and invoice hints across Indian & Global banks."""
    combined = f"{subject}\n{body}"
    
    # 1. Multi-bank Amount extraction engine
    # Supports Rs, INR, ₹, USD $, EUR €, GBP £, credited by, received amount of
    amount_patterns = [
        r'(?:credited|received|credited\s+with|amount\s+of|vPA|paid)\s*(?:Rs\.?|INR|₹|\$|€|£)?\s*([\d,]+(?:\.\d{1,2})?)',
        r'(?:Rs\.?|INR|₹|\$|€|£)\s*([\d,]+(?:\.\d{1,2})?)',
        r'([\d,]+\.\d{2})\s*(?:credited|received|paid)'
    ]
    extracted_amount = None
    for pat in amount_patterns:
        match = re.search(pat, combined, re.IGNORECASE)
        if match:
            try:
                val_str = match.group(1).replace(',', '')
                val_dec = Decimal(val_str)
                if val_dec > 0:
                    extracted_amount = str(val_dec)
                    break
            except Exception:
                continue

    # 2. Multi-bank Reference / UTR / Txn ID extraction engine
    # Supports HDFC, ICICI, SBI, Axis, Kotak, Razorpay, Stripe, PhonePe, Paytm, UPI, Wise, Wire
    ref_patterns = [
        r'(?:UTR|Ref(?:erence)?\s*(?:No|ID|Num)?|Txn\s*(?:ID|Ref|No)?|RRN|IMPS|NEFT|UPI|Payment\s*ID)[:\s#]*([a-zA-Z0-9_-]{6,35})',
        r'Ref[:\s]*([a-zA-Z0-9]{8,30})',
        r'\b([0-9]{12})\b'  # Standard 12-digit UTR/RRN number
    ]
    reference = None
    for pat in ref_patterns:
        match = re.search(pat, combined, re.IGNORECASE)
        if match:
            cand = match.group(1).strip()
            # Omit generic words if matched by 12-digit fallback
            if cand.isdigit() or len(cand) >= 8:
                reference = cand
                break

    # 3. Invoice Number hint extraction
    inv_match = re.search(r'\b(?:INV|Invoice|Bill|Order)[-_\s#:]+([a-zA-Z0-9_-]{1,30})\b', combined, re.IGNORECASE)
    invoice_hint = inv_match.group(1).strip() if inv_match else None

    # Bank detection tag
    bank_tag = "Generic"
    if "hdfc" in combined.lower():
        bank_tag = "HDFC Bank"
    elif "icici" in combined.lower():
        bank_tag = "ICICI Bank"
    elif "sbi" in combined.lower() or "state bank" in combined.lower():
        bank_tag = "SBI"
    elif "axis" in combined.lower():
        bank_tag = "Axis Bank"
    elif "kotak" in combined.lower():
        bank_tag = "Kotak Bank"
    elif "razorpay" in combined.lower():
        bank_tag = "Razorpay"
    elif "stripe" in combined.lower():
        bank_tag = "Stripe"
    elif "phonepe" in combined.lower():
        bank_tag = "PhonePe"
    elif "paytm" in combined.lower():
        bank_tag = "Paytm"
    elif "wise" in combined.lower():
        bank_tag = "Wise"

    return {
        "amount": extracted_amount,
        "reference": reference,
        "invoice_hint": invoice_hint,
        "bank_detected": bank_tag,
        "raw_text": combined
    }


def match_and_process_bank_email(db, store, business, subject, body, received_on=None, actor="bank_email", now=None):
    """Parse bank email, match to open invoice/installment, record receipt, halt reminders, and queue automated confirmation."""
    now = now or utcnow()
    received_date = received_on or business_today(db, business, now).isoformat()
    
    parsed = parse_bank_email(subject, body)
    if not parsed["amount"]:
        raise WorkspaceError("Could not extract a valid payment amount from the bank email.")

    payment_amount_minor = minor(parsed["amount"])
    reference = parsed["reference"] or f"BANK-{parsed['bank_detected'].replace(' ','')}-{int(now.timestamp())}"

    # Find open invoices for this business
    rows = db.execute("SELECT invoice_key, data, profile FROM ws_invoices WHERE business_id=?", (business,)).fetchall()
    candidates = []
    for r in rows:
        inv = get_invoice(db, business, r["invoice_key"])
        if inv["status"] in {"unpaid", "partially_paid"}:
            candidates.append(inv)

    if not candidates:
        raise WorkspaceError("No open invoices found for this business.")

    matched_inv = None

    # Matching Strategy 1: Extracted invoice hint
    if parsed["invoice_hint"]:
        hint = parsed["invoice_hint"].casefold()
        for inv in candidates:
            if inv["invoice_no"].casefold() == hint or hint in inv["invoice_no"].casefold():
                matched_inv = inv
                break

    # Matching Strategy 2: Exact invoice number match in email body/subject
    if not matched_inv:
        for inv in candidates:
            if inv["invoice_no"].casefold() in parsed["raw_text"].casefold():
                matched_inv = inv
                break

    # Matching Strategy 3: Exact outstanding amount match if unique
    if not matched_inv:
        matching_amount_invs = [inv for inv in candidates if minor(inv["outstanding_amount"]) == payment_amount_minor]
        if len(matching_amount_invs) == 1:
            matched_inv = matching_amount_invs[0]

    # Matching Strategy 4: Customer email or name match
    if not matched_inv:
        for inv in candidates:
            if (inv["email"] and inv["email"].casefold() in parsed["raw_text"].casefold()) or \
               (inv["client_name"] and inv["client_name"].casefold() in parsed["raw_text"].casefold()):
                matched_inv = inv
                break

    if not matched_inv:
        raise WorkspaceError("Could not automatically match bank email payment to any open invoice.")

    # Record receipt in ledger
    recording_amount = min(payment_amount_minor, minor(matched_inv["outstanding_amount"]))
    rec_val = amount(recording_amount)

    receipt_id = record_receipt(
        db, store, business, matched_inv["invoice_no"], rec_val, "payment", reference, received_date, actor, now
    )

    # Re-fetch updated invoice
    updated_inv = get_invoice(db, business, matched_inv["invoice_no"])

    # Smart Installment Allocation Check
    from business_features import installments_due
    today_dt = business_today(db, business, now)
    installments_left = installments_due(db, business, matched_inv["invoice_no"], updated_inv, today_dt) or []

    # Automated Payment Confirmation Message Queueing
    confirmation_queued = False
    if updated_inv["email"] and "email" in updated_inv["consents"]:
        confirmation_subject = f"Receipt Confirmation: Payment Received for {updated_inv['invoice_no']}"
        confirmation_body = (
            f"Dear {updated_inv['client_name']},\n\n"
            f"Thank you! We have received your payment of {updated_inv['currency']} {rec_val} (Ref: {reference}).\n"
            f"Invoice: {updated_inv['invoice_no']}\n"
            f"Remaining Outstanding: {updated_inv['currency']} {updated_inv['outstanding_amount']}\n\n"
            f"We appreciate your prompt business."
        )
        job_id = secrets.token_hex(16)
        payload = {
            "business_id": business,
            "invoice_key": updated_inv["invoice_no"].casefold(),
            "recipient": updated_inv["email"],
            "subject": confirmation_subject,
            "body": confirmation_body,
            "channel": "email",
            "cooldown_days": 1,
            "expires": (now + datetime.resolution).isoformat() if hasattr(datetime, "resolution") else (now).isoformat()
        }
        db.execute(
            """INSERT INTO ws_jobs(id,business_id,invoice_key,rule_id,unique_key,channel,mode,state,payload,due_at,created,authorized_by)
               VALUES(?,?,?,NULL,?,?,'live','queued',?,?,?,?)""",
            (job_id, business, updated_inv["invoice_no"].casefold(), f"confirm-{job_id}", "email", encode(payload), now.isoformat(), now.isoformat(), actor)
        )
        confirmation_queued = True

    return {
        "receipt_id": receipt_id,
        "invoice_no": updated_inv["invoice_no"],
        "client_name": updated_inv["client_name"],
        "amount_recorded": rec_val,
        "reference": reference,
        "bank_detected": parsed["bank_detected"],
        "new_status": updated_inv["status"],
        "reminders_stopped": updated_inv["status"] == "paid",
        "installments_remaining": len(installments_left),
        "confirmation_queued": confirmation_queued
    }


def poll_imap_inbox(store, business, host, username, password, port=993, ssl=True, folder="INBOX", actor="imap_poller"):
    """Connects securely to an IMAP inbox, fetches unread bank credit emails, and processes payments."""
    processed = []
    try:
        M = imaplib.IMAP4_SSL(host, port) if ssl else imaplib.IMAP4(host, port)
        M.login(username, password)
        M.select(folder)
        
        # Search unread messages
        status, response = M.search(None, '(UNSEEN)')
        if status != "OK" or not response[0]:
            M.logout()
            return []

        msg_nums = response[0].split()
        for num in msg_nums:
            status, data = M.fetch(num, '(RFC822)')
            if status != "OK":
                continue
            raw_email = data[0][1]
            msg = email.message_from_bytes(raw_email)
            
            # Decode Subject
            subject = ""
            raw_subj = msg.get("Subject")
            if raw_subj:
                dh = decode_header(raw_subj)
                subject = "".join(str(t[0], t[1] or "utf-8") if isinstance(t[0], bytes) else str(t[0]) for t in dh)

            # Get Body
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="ignore")
                        break
            else:
                body = msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", errors="ignore")

            with store.transaction() as db:
                try:
                    res = match_and_process_bank_email(db, store, business, subject, body, actor=actor)
                    processed.append(res)
                    # Mark email as read/seen
                    M.store(num, '+FLAGS', '\\Seen')
                except Exception:
                    # Skip emails that don't match bank payments
                    pass

        M.logout()
    except Exception as exc:
        raise WorkspaceError(f"IMAP Connection failed: {exc}") from exc

    return processed
