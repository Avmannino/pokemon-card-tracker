"""Grading opportunities: raw cards you own whose PSA 9 / PSA 10 prices are
well above their raw price.

A market-opportunity tool only - it can't know what grade a card would get.
Everything comes from prices already stored (the collection's own
valuation), so opening the screen never calls a pricing API.

Variant safety: a card's raw, PSA 9 and PSA 10 values all come from that
card's own price history, and each card row is exactly one printing and
variant (see poketrace.match_exact). Graded data that can't be tied to the
variant (TCGGO on a product shared by several variants) is already left out
by valuation.evaluate, so such a card shows as unavailable rather than with
another variant's prices.
"""

from typing import Any


# Default cost of grading one card (fees, shipping, insurance). The page can
# override it per request; GRADING_COST in the backend environment changes
# the default (see config.py).
DEFAULT_GRADING_COST = 30.0

# The opportunity score (0-100) is three plain parts:
#
#   PSA 10 upside   up to 60 points: 60 x (PSA 10 net upside / $150),
#                   capped at 60, where net upside = PSA 10 - raw - grading.
#   PSA 9 safety    up to 30 points: 30 x (PSA 9 / (raw + grading)), capped
#                   at 30 - how much of what you put in a PSA 9 gives back.
#                   0 when the PSA 9 price is unknown.
#   Price data      up to 10 points: 10 x (sales behind the graded prices / 5),
#                   capped at 10; 5 when the count isn't known (e.g. a price
#                   you entered).
#
# PSA population / gem rate isn't stored anywhere yet, so it isn't scored.
UPSIDE_POINTS = 60
UPSIDE_FULL_AT = 150.0
SAFETY_POINTS = 30
DATA_POINTS = 10
DATA_FULL_AT_SALES = 5

SCORE_EXPLANATION = (
    "Score out of 100: up to 60 for PSA 10 net upside (full at +$150 after "
    "grading), up to 30 for how much of the raw value plus grading cost a "
    "PSA 9 gives back, and up to 10 for how many sales the graded prices "
    "come from. Labels: Not Worth Grading when even a PSA 10 doesn't cover "
    "the raw value plus grading; High Risk when a PSA 9 would lose money (or "
    "its price is unknown); otherwise Excellent (75+), Strong (55+) or "
    "Moderate. Market opportunity only - actual grade depends on card "
    "condition."
)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _grade_value(values: dict[str, Any] | None) -> dict[str, Any]:
    """The parts of a grade's valuation (valuation.build_market_values) the
    analysis uses."""
    values = values or {}
    sales = [
        component.get("sale_count")
        for component in values.get("components") or []
        if component.get("role") in ("primary", "supporting")
        and component.get("sale_count") is not None
    ]
    return {
        "value": values.get("estimate"),
        "method": values.get("method"),
        "summary": values.get("summary"),
        "as_of": values.get("last_updated"),
        "sales": max(sales) if sales else None,
    }


def _language(card: dict[str, Any]) -> str:
    return "Japanese" if "(Japanese)" in (card.get("name") or "") else "English"


def inputs_from_collection(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per raw card you own, from the serialized collection (main.
    serialize_collection_item). Several rows of the same card are one entry
    with their quantities added up."""
    by_card: dict[str, dict[str, Any]] = {}

    for item in items:
        if item.get("ownership_grade") != "RAW":
            continue

        card = item["card"]
        quantity = int(item.get("quantity") or 1)
        if card["id"] in by_card:
            by_card[card["id"]]["quantity"] += quantity
            continue

        values = item.get("market_values") or {}
        by_card[card["id"]] = {
            "collection_item_id": item["id"],
            "card": {
                key: card.get(key)
                for key in ("id", "name", "card_number", "set_name", "variant", "rarity", "image_url")
            },
            "language": _language(card),
            "quantity": quantity,
            "small_hit": bool(item.get("small_hit")),
            "raw": _grade_value(values.get("RAW")),
            "psa9": _grade_value(values.get("PSA_9")),
            "psa10": _grade_value(values.get("PSA_10")),
        }

    return list(by_card.values())


def evaluate(entry: dict[str, Any], grading_cost: float) -> dict[str, Any]:
    """One raw card's grading outlook at `grading_cost` per card."""
    raw = entry["raw"]["value"]
    psa9 = entry["psa9"]["value"]
    psa10 = entry["psa10"]["value"]

    result: dict[str, Any] = {
        "collection_item_id": entry["collection_item_id"],
        "card": entry["card"],
        "language": entry["language"],
        "quantity": entry["quantity"],
        "small_hit": entry["small_hit"],
        "raw_value": raw,
        "psa9_value": psa9,
        "psa10_value": psa10,
        "grading_cost": round(grading_cost, 2),
        "graded_as_of": entry["psa10"]["as_of"],
        "graded_source": entry["psa10"]["summary"],
        "psa9_source": entry["psa9"]["summary"],
        "available": False,
        "unavailable_reason": None,
        "psa10_premium": None,
        "psa10_premium_pct": None,
        "psa10_multiple": None,
        "psa10_net": None,
        "psa9_premium": None,
        "psa9_multiple": None,
        "psa9_net": None,
        "score": None,
        "label": None,
        "risk_note": None,
        "score_parts": [],
    }

    if raw is None:
        result["unavailable_reason"] = "No raw price yet."
        return result
    if raw <= 0:
        result["unavailable_reason"] = "Raw price is $0, so premiums can't be measured."
        return result
    if psa10 is None:
        result["unavailable_reason"] = (
            "Graded pricing unavailable. " + (entry["psa10"]["summary"] or "")
        ).strip()
        return result

    psa10_net = psa10 - raw - grading_cost
    psa9_net = psa9 - raw - grading_cost if psa9 is not None else None

    upside = UPSIDE_POINTS * _clamp(psa10_net / UPSIDE_FULL_AT)
    safety = (
        SAFETY_POINTS * _clamp(psa9 / (raw + grading_cost))
        if psa9 is not None and raw + grading_cost > 0
        else 0.0
    )
    sales_counts = [
        count
        for count in (entry["psa10"]["sales"], entry["psa9"]["sales"] if psa9 is not None else None)
        if count is not None
    ]
    sales = min(sales_counts) if sales_counts else None
    data = (
        DATA_POINTS * _clamp(sales / DATA_FULL_AT_SALES)
        if sales is not None
        else DATA_POINTS / 2
    )
    score = round(upside + safety + data)

    if psa10_net <= 0:
        label = "Not Worth Grading"
        risk_note = "Even a PSA 10 wouldn't cover the raw value plus grading."
    elif psa9_net is None:
        label = "High Risk"
        risk_note = "PSA 9 price unknown, so the downside can't be checked."
    elif psa9_net < 0:
        label = "High Risk"
        risk_note = (
            f"A PSA 9 would lose ${-psa9_net:,.2f} after grading"
            + (" - high upside, but PSA 10 dependent." if psa10_net >= 100 else " - PSA 10 dependent.")
        )
    else:
        label = (
            "Excellent Opportunity" if score >= 75
            else "Strong Opportunity" if score >= 55
            else "Moderate Opportunity"
        )
        risk_note = "A PSA 9 still covers the raw value and grading cost."

    result.update(
        {
            "available": True,
            "psa10_premium": round(psa10 - raw, 2),
            "psa10_premium_pct": round((psa10 - raw) / raw * 100, 1),
            "psa10_multiple": round(psa10 / raw, 2),
            "psa10_net": round(psa10_net, 2),
            "psa9_premium": round(psa9 - raw, 2) if psa9 is not None else None,
            "psa9_multiple": round(psa9 / raw, 2) if psa9 is not None else None,
            "psa9_net": round(psa9_net, 2) if psa9_net is not None else None,
            "score": score,
            "label": label,
            "risk_note": risk_note,
            "score_parts": [
                {
                    "name": "PSA 10 upside",
                    "points": round(upside, 1),
                    "max": UPSIDE_POINTS,
                    "detail": f"Nets {'+' if psa10_net >= 0 else '-'}${abs(psa10_net):,.2f} after grading.",
                },
                {
                    "name": "PSA 9 safety",
                    "points": round(safety, 1),
                    "max": SAFETY_POINTS,
                    "detail": (
                        f"A PSA 9 gives back {psa9 / (raw + grading_cost):.0%} of raw + grading."
                        if psa9 is not None
                        else "PSA 9 price unknown."
                    ),
                },
                {
                    "name": "Price data",
                    "points": round(data, 1),
                    "max": DATA_POINTS,
                    "detail": (
                        f"Graded prices from {sales} sale{'s' if sales != 1 else ''}."
                        if sales is not None
                        else "Sale count unknown (a price you entered)."
                    ),
                },
            ],
        }
    )
    return result


def report(
    inputs: list[dict[str, Any]],
    grading_cost: float,
    default_grading_cost: float = DEFAULT_GRADING_COST,
) -> dict[str, Any]:
    """Every raw card's outlook, the most interesting first: available ones
    by score (then PSA 10 net upside), then the ones without graded prices."""
    rows = [evaluate(entry, grading_cost) for entry in inputs]
    rows.sort(
        key=lambda row: (
            not row["available"],
            -(row["score"] or 0),
            -(row["psa10_net"] or 0),
            -(row["raw_value"] or 0),
        )
    )

    return {
        "grading_cost": round(grading_cost, 2),
        "default_grading_cost": round(default_grading_cost, 2),
        "raw_cards": len(rows),
        "available": sum(1 for row in rows if row["available"]),
        "score_explanation": SCORE_EXPLANATION,
        "opportunities": rows,
    }
