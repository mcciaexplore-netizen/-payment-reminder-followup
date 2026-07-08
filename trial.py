"""
trial.py — IP-based trial management
──────────────────────────────────────
Tracks when each IP first accessed the app and enforces a 5-minute limit.

Storage:
  - HuggingFace Spaces: writes to /data (enable Persistent Storage in Space settings)
  - Local dev         : writes to ./data/
  In both cases, if the file can't be written, the trial still works
  for the current session via the in-memory fallback dict.
"""

import json
import time
from pathlib import Path

import streamlit as st

# ── Config ────────────────────────────────────────────────────
TRIAL_SECONDS = 300   # 5 minutes

# HuggingFace Spaces mounts persistent storage at /data
_HF_DATA   = Path("/data")
_LOCAL_DATA = Path("data")
DATA_DIR    = _HF_DATA if _HF_DATA.exists() else _LOCAL_DATA
DATA_FILE   = DATA_DIR / "ip_trials.json"

# In-memory fallback (survives Streamlit reruns via @st.cache_resource)
@st.cache_resource
def _memory_store() -> dict:
    return {}


# ── Persistence helpers ───────────────────────────────────────

def _load() -> dict:
    """Load IP records from JSON file, fall back to in-memory store."""
    try:
        if DATA_FILE.exists():
            return json.loads(DATA_FILE.read_text())
    except Exception:
        pass
    return dict(_memory_store())   # copy from memory fallback


def _save(data: dict) -> None:
    """Persist IP records to JSON file and update in-memory store."""
    _memory_store().update(data)   # always update memory
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        DATA_FILE.write_text(json.dumps(data, indent=2))
    except Exception:
        pass   # memory fallback is already updated above


# ── IP detection ──────────────────────────────────────────────

def get_client_ip() -> str:
    """
    Extract the real client IP from Streamlit request headers.
    On HuggingFace, the real IP is in X-Forwarded-For.
    Falls back to 'local' for local dev (all local sessions share one slot).
    """
    try:
        headers   = st.context.headers
        forwarded = headers.get("X-Forwarded-For", "")
        if forwarded:
            # X-Forwarded-For can be "client, proxy1, proxy2" — take first
            return forwarded.split(",")[0].strip()
        real_ip = headers.get("X-Real-IP", "")
        if real_ip:
            return real_ip
    except Exception:
        pass
    return "local"


# ── Trial check ───────────────────────────────────────────────

def check_ip(ip: str) -> dict:
    """
    Check whether an IP address is within its trial period.

    Returns a dict:
      allowed          : bool   — False means show the blocked screen
      seconds_remaining: int    — 0 if blocked
      elapsed          : int    — seconds since first visit
      is_new           : bool   — True on very first visit
      minutes          : int    — for display
      seconds          : int    — remainder seconds for display (MM:SS)
    """
    data = _load()
    now  = time.time()

    if ip in data:
        record    = data[ip]
        elapsed   = int(now - record["first_seen"])
        remaining = max(0, TRIAL_SECONDS - elapsed)

        if remaining == 0:
            data[ip]["blocked"] = True
            _save(data)
            return {
                "allowed": False, "seconds_remaining": 0,
                "elapsed": elapsed, "is_new": False,
                "minutes": 0, "seconds": 0,
            }

        return {
            "allowed": True,  "seconds_remaining": remaining,
            "elapsed": elapsed, "is_new": False,
            "minutes": remaining // 60, "seconds": remaining % 60,
        }

    else:
        # First visit — register IP
        data[ip] = {"first_seen": now, "blocked": False}
        _save(data)
        return {
            "allowed": True, "seconds_remaining": TRIAL_SECONDS,
            "elapsed": 0, "is_new": True,
            "minutes": TRIAL_SECONDS // 60, "seconds": 0,
        }
