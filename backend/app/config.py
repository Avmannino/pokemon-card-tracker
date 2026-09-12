import os
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH)


class Settings:
    def __init__(self) -> None:
        self.supabase_url = os.getenv("SUPABASE_URL", "").strip()
        self.supabase_secret_key = os.getenv("SUPABASE_SECRET_KEY", "").strip()
        self.poketrace_api_key = os.getenv("POKETRACE_API_KEY", "").strip()
        self.frontend_origin = os.getenv(
            "FRONTEND_ORIGIN",
            "http://localhost:5173",
        ).strip()

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
