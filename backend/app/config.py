import os
from datetime import time
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH)


def _clock_time(name: str, default: str) -> time:
    raw = os.getenv(name, default).strip()

    try:
        return time.fromisoformat(raw)
    except ValueError as exc:
        raise RuntimeError(
            f"{name} must look like HH:MM (24-hour), got {raw!r}."
        ) from exc


class Settings:
    def __init__(self) -> None:
        self.supabase_url = os.getenv("SUPABASE_URL", "").strip()
        self.supabase_secret_key = os.getenv("SUPABASE_SECRET_KEY", "").strip()
        self.poketrace_api_key = os.getenv("POKETRACE_API_KEY", "").strip()
        # Optional: graded (PSA) prices are only pulled when this is set.
        self.rapidapi_key = os.getenv("RAPIDAPI_KEY", "").strip()
        self.frontend_origin = os.getenv(
            "FRONTEND_ORIGIN",
            "http://localhost:5173",
        ).strip()

        # The only two times of day (server-local) that prices are pulled
        # from PokeTrace.
        self.refresh_am_time = _clock_time("REFRESH_AM_TIME", "08:07")
        self.refresh_timezone = os.getenv(
            "REFRESH_TIMEZONE", "America/New_York"
        ).strip()
        self.refresh_pm_time = _clock_time("REFRESH_PM_TIME", "20:07")

        missing = []
        if not self.supabase_url:
            missing.append("SUPABASE_URL")
        if not self.supabase_secret_key:
            missing.append("SUPABASE_SECRET_KEY")
        if not self.poketrace_api_key:
            missing.append("POKETRACE_API_KEY")

        if missing:
            joined = ", ".join(missing)
            raise RuntimeError(
                f"Missing required environment variables: {joined}. "
                "Copy backend/.env.example to backend/.env and fill them in."
            )


settings = Settings()
