"""
tool_schemas.py
Tool definitions in OpenAI / Groq format.
  Claude API uses:  {"name": ..., "input_schema": {...}}
  Groq / OpenAI:   {"type": "function", "function": {"name": ..., "parameters": {...}}}

This file uses the Groq format since we are on the free tier.
"""

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_invoices",
            "description": (
                "Read all invoice records from an Excel (.xlsx) file. "
                "Returns every row as a JSON list. Always call this first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "xlsx_path": {
                        "type": "string",
                        "description": "Relative or absolute path to the invoices Excel file.",
                    }
                },
                "required": ["xlsx_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "filter_overdue",
            "description": (
                "Filter the invoice list to only unpaid invoices whose due date is "
                "at least `days_threshold` days in the past. "
                "Adds a `days_overdue` field to each matching record. "
                "Returns filtered list sorted by most overdue first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "invoices_json": {
                        "type": "string",
                        "description": "JSON string returned by read_invoices.",
                    },
                    "days_threshold": {
                        "type": "string",
                        "description": "Minimum days past due to include. Defaults to 7.",
                    },
                },
                "required": ["invoices_json"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "preview_and_approve",
            "description": (
                "Show the drafted email (subject + body) to the human operator in the terminal "
                "and wait for their approval before sending. "
                "ALWAYS call this before send_email — never skip it. "
                "Returns approved=true/false and the final message text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "client_name":  {"type": "string"},
                    "email":        {"type": "string", "description": "Client's email address."},
                    "invoice_no":   {"type": "string"},
                    "amount":       {"type": "string", "description": "Amount as string e.g. '45000'."},
                    "days_overdue": {"type": "string"},
                    "subject":      {"type": "string", "description": "Email subject line."},
                    "body":         {"type": "string", "description": "Full email body text."},
                },
                "required": ["client_name", "email", "invoice_no", "amount", "days_overdue", "subject", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": (
                "Send a payment reminder email via Gmail SMTP. "
                "Only call this when preview_and_approve returned approved=true. "
                "Use the subject and body returned from preview_and_approve "
                "(the operator may have edited them)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "to_email": {"type": "string", "description": "Recipient email address."},
                    "subject":  {"type": "string"},
                    "body":     {"type": "string"},
                },
                "required": ["to_email", "subject", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_reminder_sent",
            "description": (
                "Append a record of the sent reminder to the CSV log file. "
                "Call this after every successful send_email."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "invoice_no":   {"type": "string"},
                    "client_name":  {"type": "string"},
                    "email":        {"type": "string"},
                    "amount":       {"type": "string"},
                    "days_overdue": {"type": "string"},
                    "body":         {"type": "string", "description": "The email body that was sent."},
                },
                "required": ["invoice_no", "client_name", "email", "amount", "days_overdue", "body"],
            },
        },
    },
]
