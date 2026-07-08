"""
config.py
All environment variables for the free-tier payment follow-up agent.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Groq (free LLM) ──────────────────────────────────────────
GROQ_API_KEY: str  = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL:   str  = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# ── Gmail SMTP (free email) ──────────────────────────────────
# Use an App Password, NOT your Gmail login password.
# Steps: Google Account → Security → 2-Step Verification (on) → App Passwords → create one
GMAIL_USER:         str = os.getenv("GMAIL_USER", "")
GMAIL_APP_PASSWORD: str = os.getenv("GMAIL_APP_PASSWORD", "")

# ── File paths ───────────────────────────────────────────────
INVOICES_PATH = Path(os.getenv("INVOICES_PATH", "invoices.xlsx"))
LOG_PATH      = Path(os.getenv("LOG_PATH",      "reminders_log.csv"))

# ── Behaviour ────────────────────────────────────────────────
DEFAULT_OVERDUE_DAYS: int  = int(os.getenv("DEFAULT_OVERDUE_DAYS", "7"))
BUSINESS_NAME:        str  = os.getenv("BUSINESS_NAME", "Your Company")

# DRY_RUN=true  → previews emails in terminal, does NOT send
# DRY_RUN=false → actually sends emails via Gmail
DRY_RUN: bool = os.getenv("DRY_RUN", "true").lower() == "true"
