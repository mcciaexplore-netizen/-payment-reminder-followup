#!/usr/bin/env python3
"""
agent.py — Payment Follow-up Agent  (Free Tier Edition)
────────────────────────────────────────────────────────
LLM  : Groq API — llama-3.3-70b-versatile (free tier)
Email: Gmail SMTP via smtplib (free, needs App Password)
Data : Local Excel + CSV (free)

Usage
─────
  python agent.py                          # uses .env defaults
  python agent.py --file invoices.xlsx     # explicit file
  python agent.py --days 14               # change overdue threshold
  python agent.py --no-dry-run            # actually send emails

Flow
────
  read_invoices → filter_overdue → (for each) →
  preview_and_approve → send_email → log_reminder_sent
"""

import argparse
import json
import sys

from groq import Groq

from config import (
    GROQ_API_KEY,
    GROQ_MODEL,
    BUSINESS_NAME,
    DEFAULT_OVERDUE_DAYS,
    DRY_RUN,
    INVOICES_PATH,
)
from tool_schemas import TOOL_SCHEMAS
from tools import (
    filter_overdue,
    log_reminder_sent,
    preview_and_approve,
    read_invoices,
    send_email,
)

# ─────────────────────────────────────────────────────────────
# System prompt
# ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = f"""You are a payment follow-up agent for {BUSINESS_NAME}.

Your exact steps:
1. Call read_invoices with the provided file path.
2. Call filter_overdue on the result.
3. If overdue_count is 0, say so and stop.
4. For each overdue invoice, do steps 5-7:
5. Draft a professional payment reminder email (subject + body).
6. Call preview_and_approve — NEVER skip this step.
7. If approved is true: call send_email with the subject and body
   returned by preview_and_approve (not your own draft — the operator
   may have edited it). Then call log_reminder_sent.
8. After all invoices, print a summary: sent count, skipped count,
   total overdue amount across sent reminders.

Email drafting rules:
- Subject: "Payment Reminder: Invoice {{invoice_no}} — ₹{{amount}} Overdue"
- Open with "Dear [Name],"
- State the invoice number, exact amount (₹ with Indian comma formatting
  e.g. ₹1,45,000), and number of days overdue.
- Be polite but clear. One short paragraph is enough.
- Request payment within 3 working days or ask them to contact you
  if there is a query.
- Close with "Warm regards," then "{BUSINESS_NAME}".
- NO markdown in the email body — plain text only.
- Keep the body under 150 words.
"""

# ─────────────────────────────────────────────────────────────
# Tool dispatcher
# ─────────────────────────────────────────────────────────────

def run_tool(name: str, inputs: dict) -> str:
    dispatch = {
        "read_invoices": lambda i: read_invoices(
            i["xlsx_path"]
        ),
        "filter_overdue": lambda i: filter_overdue(
            i["invoices_json"],
            int(i.get("days_threshold", DEFAULT_OVERDUE_DAYS)),
        ),
        "preview_and_approve": lambda i: preview_and_approve(
            i["client_name"],
            i["email"],
            i["invoice_no"],
            i["amount"],
            int(i["days_overdue"]),
            i["subject"],
            i["body"],
        ),
        "send_email": lambda i: send_email(
            i["to_email"],
            i["subject"],
            i["body"],
        ),
        "log_reminder_sent": lambda i: log_reminder_sent(
            i["invoice_no"],
            i["client_name"],
            i["email"],
            i["amount"],
            int(i["days_overdue"]),
            i["body"],
        ),
    }
    fn = dispatch.get(name)
    if not fn:
        return json.dumps({"status": "error", "message": f"Unknown tool: {name}"})
    return fn(inputs)


# ─────────────────────────────────────────────────────────────
# Agentic loop
# ─────────────────────────────────────────────────────────────

def run_agent(xlsx_path: str, days_threshold: int) -> None:
    if not GROQ_API_KEY:
        print("ERROR: GROQ_API_KEY not set. Get a free key at console.groq.com")
        sys.exit(1)

    client = Groq(api_key=GROQ_API_KEY)

    print(f"\n{'─' * 60}")
    print(f"  Payment Follow-up Agent  —  {BUSINESS_NAME}")
    print(f"  LLM      : {GROQ_MODEL}  (Groq free tier)")
    print(f"  File     : {xlsx_path}")
    print(f"  Threshold: {days_threshold} days overdue")
    print(f"  Mode     : {'DRY RUN — emails will NOT be sent' if DRY_RUN else 'LIVE — emails WILL be sent'}")
    print(f"{'─' * 60}\n")

    # Groq (OpenAI format) puts the system prompt in messages[0]
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Process overdue invoices from '{xlsx_path}'. "
                f"Flag and send reminders for invoices at least {days_threshold} days past due."
            ),
        },
    ]

    MAX_ITERATIONS = 40   # safety brake — prevents infinite loops

    for iteration in range(MAX_ITERATIONS):
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
            max_tokens=4096,
            temperature=0.1,   # low temp = more deterministic tool calling
        )

        msg    = response.choices[0].message
        finish = response.choices[0].finish_reason

        # ── Print any text the model outputs ──────────────────
        if msg.content and msg.content.strip():
            print(f"\n[Agent] {msg.content.strip()}\n")

        # ── Build assistant entry for message history ──────────
        # Must include tool_calls in the dict if present,
        # otherwise Groq will reject the follow-up tool_result messages.
        assistant_entry: dict = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            assistant_entry["tool_calls"] = [
                {
                    "id":   tc.id,
                    "type": "function",
                    "function": {
                        "name":      tc.function.name,
                        "arguments": tc.function.arguments,   # already a JSON string
                    },
                }
                for tc in msg.tool_calls
            ]
        messages.append(assistant_entry)

        # ── Terminal condition ─────────────────────────────────
        if finish == "stop" or not msg.tool_calls:
            break

        # ── Execute each tool call, append results ─────────────
        for tc in msg.tool_calls:
            fn_name = tc.function.name

            try:
                fn_args = json.loads(tc.function.arguments)
            except json.JSONDecodeError as exc:
                fn_args = {}
                print(f"  [WARN] Could not parse args for {fn_name}: {exc}")

            # Print a concise call trace
            arg_str = ", ".join(
                f"{k}={repr(v)[:45]}" for k, v in fn_args.items()
            )
            print(f"  → {fn_name}({arg_str})")

            result_str = run_tool(fn_name, fn_args)

            # Surface errors clearly
            try:
                r = json.loads(result_str)
                if r.get("status") == "error":
                    print(f"  [ERROR] {r['message']}")
            except Exception:
                pass

            messages.append({
                "role":         "tool",
                "tool_call_id": tc.id,
                "content":      result_str,
            })

    else:
        print(f"[Agent] Reached iteration limit ({MAX_ITERATIONS}). Stopping.")

    print(f"\n{'─' * 60}")
    print("  Agent finished.")
    print(f"{'─' * 60}\n")


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Payment Follow-up Agent (free tier: Groq + Gmail)",
    )
    parser.add_argument(
        "--file", "-f",
        default=str(INVOICES_PATH),
        help=f"Path to invoices Excel (default: {INVOICES_PATH})",
    )
    parser.add_argument(
        "--days", "-d",
        type=int,
        default=DEFAULT_OVERDUE_DAYS,
        help=f"Overdue threshold in days (default: {DEFAULT_OVERDUE_DAYS})",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Override DRY_RUN and actually send emails.",
    )
    args = parser.parse_args()

    if args.no_dry_run:
        import config, tools
        config.DRY_RUN = False
        tools.DRY_RUN  = False

    run_agent(args.file, args.days)


if __name__ == "__main__":
    main()
