"""Configuration defaults; per-session settings use immutable values."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import dotenv_values, load_dotenv, set_key

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
load_dotenv(ENV_PATH)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GMAIL_USER = os.getenv("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "Your Company")
BUSINESS_ID = os.getenv("BUSINESS_ID", "local-business")
INVOICES_PATH = Path(os.getenv("INVOICES_PATH", str(ROOT / "invoices.xlsx")))
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(ROOT / ".data" / "reminders.sqlite3")))
DEFAULT_OVERDUE_DAYS = int(os.getenv("DEFAULT_OVERDUE_DAYS", "7"))
REMINDER_COOLDOWN_DAYS = int(os.getenv("REMINDER_COOLDOWN_DAYS", "7"))
TIMEZONE = os.getenv("BUSINESS_TIMEZONE", "Asia/Kolkata")
DRY_RUN = os.getenv("DRY_RUN", "true").strip().lower() != "false"
IS_DEMO = bool(os.getenv("SPACE_ID")) or os.getenv("APP_MODE", "local") == "demo"


@dataclass(frozen=True)
class SendSettings:
    business_id: str = BUSINESS_ID
    sender: str = GMAIL_USER
    password: str = field(default=GMAIL_APP_PASSWORD, repr=False)
    dry_run: bool = DRY_RUN
    preview_only: bool = IS_DEMO
    days_threshold: int = DEFAULT_OVERDUE_DAYS
    cooldown_days: int = REMINDER_COOLDOWN_DAYS
    timezone: str = TIMEZONE

    def __post_init__(self):
        if not self.business_id.strip():
            raise ValueError("A business identifier is required.")
        for value, name in [(self.days_threshold, "Overdue threshold"), (self.cooldown_days, "Reminder cooldown")]:
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 365:
                raise ValueError(f"{name} must be between 1 and 365 days.")
        ZoneInfo(self.timezone)


def read_local_settings(path: Path = ENV_PATH) -> dict[str, str]:
    """New browser sessions see saved settings without mutating process defaults."""
    defaults = {"BUSINESS_NAME": BUSINESS_NAME, "GMAIL_USER": GMAIL_USER,
                "GMAIL_APP_PASSWORD": GMAIL_APP_PASSWORD, "GROQ_API_KEY": GROQ_API_KEY}
    if IS_DEMO:
        return {key: "" for key in defaults}
    saved = dotenv_values(path)
    return {key: saved.get(key, value) or "" for key, value in defaults.items()}


def save_local_settings(values: dict[str, str], path: Path = ENV_PATH) -> None:
    if IS_DEMO:
        raise ValueError("Hosted demos cannot save credentials.")
    allowed = {"GROQ_API_KEY", "GMAIL_USER", "GMAIL_APP_PASSWORD", "BUSINESS_NAME"}
    if set(values) - allowed:
        raise ValueError("Unsupported setting.")
    path = Path(path)
    path.touch(mode=0o600, exist_ok=True)
    for key, value in values.items():
        set_key(str(path), key, value)
    if os.name != "nt":
        path.chmod(0o600)
