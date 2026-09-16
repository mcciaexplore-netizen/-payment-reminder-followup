"""Optional AI wording. Exact invoice facts are always rendered by application code."""
import json
from dataclasses import dataclass
from decimal import Decimal
from reminders import validate_message


@dataclass(frozen=True)
class Draft:
    subject: str
    body: str
    source: str


def draft_email(invoice, business_name, api_key="", model="llama-3.3-70b-versatile", *, client=None):
    name = (invoice.get("client_name") or "Customer").strip() or "Customer"
    balance = f"{Decimal(invoice['outstanding_amount']):,.2f}"
    currency = invoice.get("currency", "INR")
    closing = "Please arrange payment within three working days, or contact us if you have already paid or have a query."
    source = "template"
    # No contact data, invoice notes, credentials or balances are put in AI prompts.
    if api_key or client is not None:
        try:
            if client is None:
                from groq import Groq
                client = Groq(api_key=api_key, timeout=15, max_retries=1)
            response = client.chat.completions.create(
                model=model, messages=[{"role": "user", "content":
                    'Return JSON with a "closing" string: one polite English sentence asking for payment within three working days, or a reply if payment was made or disputed. No amounts, dates, links, contact details, threats or signatures.'}],
                max_tokens=120, temperature=0.2, response_format={"type": "json_object"})
            data = json.loads(response.choices[0].message.content)
            text = data.get("closing") if isinstance(data, dict) else None
            if not isinstance(text, str) or not 20 <= len(text.strip()) <= 350 or any(c.isdigit() for c in text) or any(x in text.lower() for x in ("http", "www.", "@", "\n", "\r", "\x00")):
                raise ValueError("Invalid AI draft shape")
            closing, source = text.strip(), "AI-assisted"
        except Exception:
            pass
    subject = f"Payment reminder: {invoice['invoice_no']}"
    body = (f"Dear {name},\n\nInvoice {invoice['invoice_no']} has an outstanding balance of "
            f"{currency} {balance}, due on {invoice['due_date']} "
            f"({invoice.get('days_overdue', 0)} days overdue).\n\n{closing}\n\nWarm regards,\n{business_name}")
    validate_message(subject, body)
    return Draft(subject, body, source)
