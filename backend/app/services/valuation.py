from statistics import median
from typing import Any


GRADES = ["RAW", "PSA_7", "PSA_8", "PSA_9", "PSA_10"]


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_market_values(
    snapshots: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    # Supabase query returns newest first. Keep only the newest record from
    # each source for a given grade so one frequently refreshed source cannot
    # overpower the others.
    latest_by_key: dict[tuple[str, str], dict[str, Any]] = {}

    for snapshot in snapshots:
        grade = snapshot.get("grade")
        source = snapshot.get("source")
        if not grade or not source:
            continue

        key = (grade, source)
        if key not in latest_by_key:
            latest_by_key[key] = snapshot

    result: dict[str, dict[str, Any]] = {}

    for grade in GRADES:
        sources = [
            row
            for (row_grade, _), row in latest_by_key.items()
            if row_grade == grade
        ]

        # A value you entered yourself always wins over an automated feed for
        # that grade, rather than being averaged into it — otherwise an
        # unreliable auto-pulled price (e.g. TCGGO re-writing itself every
        # sync) quietly drags a manually-verified price back toward it.
        # Multiple manual entries still blend together, since that's a
        # legitimate multi-source comp.
        manual_sources = [
            row
            for row in sources
            if (row.get("metadata") or {}).get("entry_method") == "manual"
        ]
        is_manual = bool(manual_sources)
        sources_for_estimate = manual_sources if is_manual else sources

        numeric_values = [
            value
            for value in (_as_float(row.get("value")) for row in sources_for_estimate)
            if value is not None
        ]

        estimate = round(float(median(numeric_values)), 2) if numeric_values else None

        # So "Manual" in the UI can link straight back to where the price
        # was checked. With more than one manual source, link the most
        # recently entered one.
        manual_url = None
        if is_manual:
            manual_url = max(
                manual_sources, key=lambda row: row.get("observed_at") or ""
            ).get("source_url")

        if is_manual:
            confidence = "MANUAL"
        elif len(numeric_values) >= 3:
            confidence = "HIGH"
        elif len(numeric_values) == 2:
            confidence = "MEDIUM"
        elif len(numeric_values) == 1:
            confidence = "LOW"
        else:
            confidence = "NONE"

        result[grade] = {
            "estimate": estimate,
            "confidence": confidence,
            "is_manual": is_manual,
            "source_url": manual_url,
            "source_count": len(numeric_values),
            "sources": [
                {
                    "source": row.get("source"),
                    "value": _as_float(row.get("value")),
                    "source_url": row.get("source_url"),
                    "observed_at": row.get("observed_at"),
                    "metadata": row.get("metadata") or {},
                }
                for row in sources
            ],
        }

    return result
