"""How a card's market value is worked out from its price snapshots.

One rule set, used for the current price and replayed for price history, so
the chart and today's value always agree.

For every grade, only the newest snapshot from each source counts. Then:

1. Manual entries win. A price you entered yourself overrides automated data
   for that grade (several manual entries are blended by median).
2. Raw cards (Near Mint only, exact variant only):
   - TCGPlayer's Near Mint market price is the primary signal.
   - eBay's Near Mint sold median confirms it. When the two agree, eBay is
     blended in (up to 50/50); the further apart they are, the less it
     counts, and past DIVERGENCE_LIMIT it doesn't count at all and the value
     is flagged as divergent. The weighting is continuous, so a price never
     jumps just because two sources crossed a threshold.
   - Other conditions (LP, MP, ...) are never used.
3. Graded cards (no manual entry): automated graded data is only used when
   it's a median of at least MIN_GRADED_SALES sales and can be tied to this
   exact variant. Otherwise the value is left unavailable, with the reason.
"""

from statistics import median
from typing import Any


GRADES = ["RAW", "PSA_7", "PSA_8", "PSA_9", "PSA_10"]

# Raw: eBay stops counting toward the estimate once it differs from
# TCGPlayer by this fraction, and the value is flagged as divergent.
DIVERGENCE_LIMIT = 0.5

# Raw: TCGPlayer and eBay within this fraction of each other is high
# confidence.
AGREEMENT_LIMIT = 0.15

# Graded: fewer sales than this isn't enough to call a market price.
MIN_GRADED_SALES = 3

NEAR_MINT = "NEAR_MINT"

EBAY_BASIS_LABELS = {
    "median30d": "30-day sold median",
    "median7d": "7-day sold median",
    "median3d": "3-day sold median",
    "avg30d": "30-day sold average",
    "avg7d": "7-day sold average",
    "avg": "latest sold average",
}


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _meta(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("metadata") or {}


def is_manual_entry(row: dict[str, Any]) -> bool:
    return _meta(row).get("entry_method") == "manual"


def grade_label(grade: str) -> str:
    return "Raw" if grade == "RAW" else grade.replace("_", " ")


def variant_label(variant: str | None) -> str | None:
    if not variant:
        return None

    return {
        "Normal": "Normal",
        "Holofoil": "Holo",
        "Reverse_Holofoil": "Reverse Holo",
        "1st_Edition": "1st Edition",
        "1st_Edition_Holofoil": "1st Edition Holo",
        "Unlimited": "Unlimited",
    }.get(variant, variant.replace("_", " "))


def variant_options(card: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The other printings of this exact card number (same TCGPlayer
    product), as last reported by PokeTrace. Empty when unknown."""
    siblings = (card or {}).get("variant_siblings") or {}
    return list(siblings.get("variants") or [])


def is_variant_ambiguous(card: dict[str, Any] | None) -> bool:
    """True when this card's TCGPlayer product covers more than one variant,
    so data keyed only by the TCGPlayer product can't be pinned to one."""
    return len({option.get("variant") for option in variant_options(card)}) > 1


def source_kind(row: dict[str, Any]) -> str:
    if is_manual_entry(row):
        return "manual"

    meta = _meta(row)
    provider = meta.get("provider")

    if provider == "PokeTrace":
        return meta.get("underlying_source") or "other"
    if provider == "TCGGO":
        return "tcggo"
    return "other"


def ebay_value(row: dict[str, Any]) -> tuple[float | None, str]:
    """The robust eBay figure for a snapshot and what it is.

    Current snapshots already store the recent sold median as their value.
    Older ones stored eBay's latest-day average (often a single sale), so
    their 30-day average is used instead, the closest robust figure they
    kept."""
    meta = _meta(row)

    if meta.get("value_basis"):
        return _as_float(row.get("value")), meta["value_basis"]

    for key, basis in (("avg_30d", "avg30d"), ("avg_7d", "avg7d")):
        value = _as_float(meta.get(key))
        if value is not None:
            return value, basis

    return _as_float(row.get("value")), "avg"


def _component(row, kind, value, role, reason=None, weight=None, label=None):
    meta = _meta(row)
    return {
        "source": row.get("source"),
        "kind": kind,
        "label": label or row.get("source"),
        "value": round(value, 2) if value is not None else None,
        "role": role,
        "reason": reason,
        "weight": round(weight, 3) if weight is not None else None,
        "sale_count": meta.get("sale_count", meta.get("sample_size")),
        "as_of": meta.get("tier_last_updated") or row.get("observed_at"),
        "source_url": row.get("source_url"),
    }


def _confidence_from_divergence(divergence: float) -> str:
    if divergence <= AGREEMENT_LIMIT:
        return "HIGH"
    if divergence < DIVERGENCE_LIMIT:
        return "MEDIUM"
    return "LOW"


def _evaluate_raw(rows: list[dict[str, Any]], variant: str | None) -> dict[str, Any]:
    components = []
    tcgplayer = None
    ebay = None
    generic = []

    for row in rows:
        kind = source_kind(row)
        meta = _meta(row)

        if kind in ("tcgplayer", "ebay"):
            row_variant = meta.get("variant")
            if variant and row_variant and row_variant != variant:
                components.append(_component(
                    row, kind, _as_float(row.get("value")), "excluded",
                    f"Recorded for the {variant_label(row_variant)} variant, "
                    f"not {variant_label(variant)}.",
                ))
                continue

            if meta.get("tier") not in (None, NEAR_MINT):
                components.append(_component(
                    row, kind, _as_float(row.get("value")), "excluded",
                    "Not Near Mint condition.",
                ))
                continue

        if kind == "tcgplayer":
            tcgplayer = row
        elif kind == "ebay":
            ebay = row
        else:
            generic.append(row)

    tcg_value = _as_float(tcgplayer.get("value")) if tcgplayer else None
    ebay_val, ebay_basis = ebay_value(ebay) if ebay else (None, None)
    ebay_label = (
        f"eBay Near Mint {EBAY_BASIS_LABELS.get(ebay_basis, ebay_basis)}"
        if ebay
        else None
    )
    tcg_label = "TCGPlayer Near Mint market"

    if tcg_value is not None and ebay_val is not None:
        divergence = abs(ebay_val - tcg_value) / tcg_value if tcg_value else float("inf")
        weight = 0.5 * max(0.0, 1 - divergence / DIVERGENCE_LIMIT)
        estimate = tcg_value + weight * (ebay_val - tcg_value)
        percent = round(divergence * 100)

        components += [
            _component(tcgplayer, "tcgplayer", tcg_value, "primary",
                       weight=1 - weight, label=tcg_label),
            _component(
                ebay, "ebay", ebay_val,
                "supporting" if weight > 0 else "excluded",
                None if weight > 0 else f"Differs from TCGPlayer by {percent}%.",
                weight=weight, label=ebay_label,
            ),
        ]

        if weight > 0:
            method = "tcgplayer_ebay_blend"
            summary = (
                f"TCGPlayer Near Mint market blended with the {ebay_label} "
                f"({round(weight * 100)}% weight; they differ by {percent}%)."
            )
        else:
            method = "tcgplayer_primary"
            summary = (
                f"TCGPlayer Near Mint market. The {ebay_label} differs by "
                f"{percent}%, so it isn't blended in."
            )

        return {
            "estimate": round(estimate, 2),
            "method": method,
            "summary": summary,
            "confidence": _confidence_from_divergence(divergence),
            "divergence": round(divergence, 4) if tcg_value else None,
            "divergent": divergence >= DIVERGENCE_LIMIT,
            "components": components,
            "used_values": [tcg_value] + ([ebay_val] if weight > 0 else []),
        }

    if tcg_value is not None:
        components.append(_component(tcgplayer, "tcgplayer", tcg_value, "primary", label=tcg_label))
        return {
            "estimate": round(tcg_value, 2),
            "method": "tcgplayer_only",
            "summary": "TCGPlayer Near Mint market (no eBay Near Mint sales to compare).",
            "confidence": "MEDIUM",
            "divergence": None,
            "divergent": False,
            "components": components,
            "used_values": [tcg_value],
        }

    if ebay_val is not None:
        components.append(_component(ebay, "ebay", ebay_val, "primary", label=ebay_label))
        return {
            "estimate": round(ebay_val, 2),
            "method": "ebay_only",
            "summary": f"{ebay_label} (no TCGPlayer Near Mint price).",
            "confidence": "LOW",
            "divergence": None,
            "divergent": False,
            "components": components,
            "used_values": [ebay_val],
        }

    return _evaluate_generic(generic, components, "No Near Mint price for this variant yet.")


def _evaluate_generic(rows, components, empty_summary) -> dict[str, Any]:
    """Median of whatever other sources there are."""
    values = []
    for row in rows:
        value = _as_float(row.get("value"))
        if value is None:
            continue
        values.append(value)
        components.append(_component(row, source_kind(row), value, "primary"))

    if not values:
        return {
            "estimate": None,
            "method": "none",
            "summary": empty_summary,
            "confidence": "NONE",
            "divergence": None,
            "divergent": False,
            "components": components,
            "used_values": [],
        }

    count = len(values)
    return {
        "estimate": round(float(median(values)), 2),
        "method": "source_median",
        "summary": "Median of the latest price from each source." if count > 1 else "Latest price from its only source.",
        "confidence": "HIGH" if count >= 3 else "MEDIUM" if count == 2 else "LOW",
        "divergence": None,
        "divergent": False,
        "components": components,
        "used_values": values,
    }


def _evaluate_graded(rows, grade, variant_ambiguous) -> dict[str, Any]:
    components = []
    usable = []
    reasons = []

    for row in rows:
        kind = source_kind(row)
        value = _as_float(row.get("value"))

        if kind != "tcggo":
            usable.append(row)
            continue

        sales = _meta(row).get("sample_size")
        label = f"eBay {grade_label(grade)} sold median (TCGGO)"

        if variant_ambiguous:
            reason = (
                "TCGGO can't tell this card's variants apart (they share one "
                "TCGPlayer product), so its graded sales may be for another variant."
            )
        elif sales is not None and int(sales) < MIN_GRADED_SALES:
            reason = (
                f"Only {sales} recent sale{'s' if int(sales) != 1 else ''} — "
                f"fewer than {MIN_GRADED_SALES} isn't enough to price."
            )
        else:
            reason = None

        if reason:
            components.append(_component(row, kind, value, "excluded", reason, label=label))
            reasons.append(reason)
            continue

        usable.append(row)
        if value is not None:
            components.append(_component(row, kind, value, "primary", label=label))

    generic_rows = [row for row in usable if source_kind(row) != "tcggo"]
    tcggo_rows = [row for row in usable if source_kind(row) == "tcggo"]

    result = _evaluate_generic(
        generic_rows,
        components,
        reasons[0] if reasons else f"No {grade_label(grade)} price yet.",
    )

    tcggo_values = [v for v in (_as_float(r.get("value")) for r in tcggo_rows) if v is not None]
    if not tcggo_values:
        return result

    values = result["used_values"] + tcggo_values
    counts = [_meta(r).get("sample_size") for r in tcggo_rows]
    sales = min(int(c) for c in counts) if all(c is not None for c in counts) else None
    only_tcggo = not result["used_values"]

    if only_tcggo:
        summary = (
            f"Median of the last {sales} eBay {grade_label(grade)} sales (via TCGGO)."
            if sales is not None
            else f"Median of recent eBay {grade_label(grade)} sales (via TCGGO)."
        )
        confidence = "MEDIUM" if sales is not None and sales >= 5 else "LOW"
    else:
        summary = result["summary"]
        confidence = result["confidence"]

    return {
        **result,
        "estimate": round(float(median(values)), 2),
        "method": "graded_sales_median" if only_tcggo else "source_median",
        "summary": summary,
        "confidence": confidence,
        "used_values": values,
    }


def evaluate(
    grade: str,
    latest_rows,
    variant: str | None = None,
    variant_ambiguous: bool = False,
) -> dict[str, Any]:
    """Value for one grade from the newest snapshot of each source, with how
    it was reached (method, summary, confidence and every source's role)."""
    rows = list(latest_rows)
    manual_rows = [row for row in rows if is_manual_entry(row)]

    if manual_rows:
        values = [v for v in (_as_float(r.get("value")) for r in manual_rows) if v is not None]
        components = [
            _component(row, "manual", _as_float(row.get("value")), "primary")
            for row in manual_rows
        ] + [
            _component(
                row, source_kind(row), _as_float(row.get("value")), "excluded",
                "Your manual price overrides automated data.",
            )
            for row in rows
            if not is_manual_entry(row)
        ]

        return {
            "estimate": round(float(median(values)), 2) if values else None,
            "method": "manual",
            "summary": (
                "Your manual price."
                if len(values) == 1
                else f"Median of your {len(values)} manual prices."
            ),
            "confidence": "MANUAL",
            "divergence": None,
            "divergent": False,
            "components": components,
            "used_values": values,
        }

    if grade == "RAW":
        return _evaluate_raw(rows, variant)

    return _evaluate_graded(rows, grade, variant_ambiguous)


def estimate_from_sources(
    latest_rows,
    grade: str = "RAW",
    variant: str | None = None,
    variant_ambiguous: bool = False,
) -> tuple[float | None, list[float]]:
    """(estimate, values it was taken from). Shared with the performance
    history so past and current values follow one rule."""
    result = evaluate(grade, latest_rows, variant, variant_ambiguous)
    return result["estimate"], result["used_values"]


def latest_by_source(snapshots: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Newest snapshot per (grade, source). Expects newest first, as the
    single-card query returns them."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}

    for snapshot in snapshots:
        grade = snapshot.get("grade")
        source = snapshot.get("source")
        if not grade or not source:
            continue

        latest.setdefault((grade, source), snapshot)

    return latest


def build_market_values(
    snapshots: list[dict[str, Any]],
    card: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Value and valuation details for every grade. `snapshots` newest
    first; `card` (optional) supplies the variant to check snapshots
    against."""
    latest = latest_by_source(snapshots)
    variant = (card or {}).get("variant")
    ambiguous = is_variant_ambiguous(card)
    result: dict[str, dict[str, Any]] = {}

    for grade in GRADES:
        sources = [row for (row_grade, _), row in latest.items() if row_grade == grade]
        valuation = evaluate(grade, sources, variant, ambiguous)

        manual_sources = [row for row in sources if is_manual_entry(row)]
        is_manual = bool(manual_sources)

        # So "Manual" in the UI can link straight back to where the price
        # was checked. With more than one manual source, link the most
        # recently entered one.
        manual_url = None
        if is_manual:
            manual_url = max(
                manual_sources, key=lambda row: row.get("observed_at") or ""
            ).get("source_url")

        last_updated = max((row.get("observed_at") or "" for row in sources), default=None) or None

        result[grade] = {
            "estimate": valuation["estimate"],
            "confidence": valuation["confidence"],
            "is_manual": is_manual,
            "source_url": manual_url,
            "source_count": len(valuation["used_values"]),
            "method": valuation["method"],
            "summary": valuation["summary"],
            "divergence": valuation["divergence"],
            "divergent": valuation["divergent"],
            "components": valuation["components"],
            "last_updated": last_updated,
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
