import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import db


logger = logging.getLogger("state_store")

# Used when the app_state table doesn't exist yet (supabase/schema.sql), so
# the app keeps working, just without state shared across processes.
FALLBACK_PATH = Path(__file__).resolve().parents[2] / ".app_state.json"

_warned = False


def _warn_once(exc: Exception) -> None:
    global _warned

    if not _warned:
        _warned = True
        logger.warning(
            "app_state table unavailable (%s); using a local file instead. "
            "Run supabase/schema.sql to share state across deploys.",
            exc,
        )


def _read_file() -> dict[str, Any]:
    try:
        return json.loads(FALLBACK_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def get(key: str) -> Any | None:
    try:
        rows = (
            db._client()
            .table("app_state")
            .select("value")
            .eq("key", key)
            .limit(1)
            .execute()
            .data
        )
        return rows[0]["value"] if rows else None
    except Exception as exc:
        _warn_once(exc)
        return _read_file().get(key)


def set(key: str, value: Any) -> None:
    try:
        db._client().table("app_state").upsert(
            {
                "key": key,
                "value": value,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="key",
        ).execute()
    except Exception as exc:
        _warn_once(exc)
        data = _read_file()
        data[key] = value
        FALLBACK_PATH.write_text(json.dumps(data, indent=2))
