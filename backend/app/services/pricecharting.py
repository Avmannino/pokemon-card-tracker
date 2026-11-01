"""PSA prices re-read from the PriceCharting page you linked a price to.

Only pages you saved as a PSA value's source URL are read, one request per
page, and only for the grade you own. A grade's value is the median of its
most recent sales listed on the page, which follows the market more closely
than PriceCharting's guide price (an average that trails a rising or
falling market) while a one-off sale can't move it. With too few recent
sales, the guide price (the page's "Full Price Guide" table) is used.
PriceCharting's Grade 8 and Grade 9 cover every grading company; its PSA 10
is PSA only.
"""

import asyncio
import re
import time
from datetime import date, timedelta
from statistics import median
from urllib.parse import urlsplit, urlunsplit

import httpx


# App grade -> PriceCharting's price guide row.
GRADE_LABELS = {
    "PSA_8": "Grade 8",
    "PSA_9": "Grade 9",
    "PSA_10": "PSA 10",
}

# Each grade's table of sold listings on a card page.
SALES_TABLES = {
    "PSA_8": "completed-auctions-new",
    "PSA_9": "completed-auctions-graded",
    "PSA_10": "completed-auctions-manual-only",
}

# The median of up to RECENT_SALES sales from the last RECENT_DAYS; fewer
# than MIN_SALES of them and the guide price is used instead.
RECENT_SALES = 7
RECENT_DAYS = 90
MIN_SALES = 3

# One page every few seconds, so a sync never hits the site in a burst.
REQUEST_SPACING_SECONDS = 3.0

USER_AGENT = "pokemon-card-tracker/1.0 (personal collection tracker)"

_last_request_at = 0.0


class PriceChartingError(RuntimeError):
    pass


class PriceChartingBlocked(PriceChartingError):
    """PriceCharting refused the request; the rest of the sync skips it."""


def page_url(url: str | None) -> str | None:
    """The card page a saved source URL points to, without its search query
    or anchor. None when it isn't a PriceCharting card page."""
    if not url:
        return None

    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()

    if parts.scheme not in ("http", "https"):
        return None
    if host != "pricecharting.com" and not host.endswith(".pricecharting.com"):
        return None
    if not parts.path.startswith("/game/"):
        return None

    return urlunsplit(("https", "www.pricecharting.com", parts.path, "", ""))


def parse_prices(html: str) -> dict[str, float | None]:
    """{grade: price} from a card page. A grade PriceCharting shows as "-"
    (no sales) is None."""
    start = html.find('id="full-prices"')
    if start == -1:
        raise PriceChartingError("No price guide found on the page.")

    section = html[start:]
    prices: dict[str, float | None] = {}
    found = 0

    for grade, label in GRADE_LABELS.items():
        match = re.search(
            r"<td>\s*" + re.escape(label) + r"\s*</td>\s*<td[^>]*>\s*([^<]*?)\s*</td>",
            section,
        )
        if match is None:
            prices[grade] = None
            continue

        found += 1
        try:
            prices[grade] = float(match.group(1).replace("$", "").replace(",", ""))
        except ValueError:
            prices[grade] = None

    if not found:
        raise PriceChartingError("The page's price guide layout has changed.")

    return prices


def parse_sales(html: str, grade: str) -> list[tuple[date, float]]:
    """A grade's sold listings on a card page, as (date, price), newest
    first."""
    start = html.find(f'class="{SALES_TABLES[grade]}"')
    if start == -1:
        return []

    section = html[start:]
    end = section.find("</table>")
    if end != -1:
        section = section[:end]

    sales = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", section, re.S):
        day = re.search(r'<td class="date">\s*(\d{4}-\d{2}-\d{2})\s*<', row)
        price = re.search(r'class="js-price"[^>]*>\s*\$([\d,]+\.\d{2})', row)
        if day and price:
            sales.append(
                (date.fromisoformat(day.group(1)), float(price.group(1).replace(",", "")))
            )

    sales.sort(key=lambda sale: sale[0], reverse=True)
    return sales


def value_from_sales(sales: list[tuple[date, float]], today: date) -> tuple[float, int] | None:
    """(median of the most recent sales, how many), or None with too few."""
    since = today - timedelta(days=RECENT_DAYS)
    recent = [price for day, price in sales if day >= since][:RECENT_SALES]
    if len(recent) < MIN_SALES:
        return None
    return round(median(recent), 2), len(recent)


def parse_values(html: str, today: date) -> dict[str, dict | None]:
    """{grade: {"value", "basis", "sale_count", "guide"}} from a card page:
    the recent-sales median ("recent_sales") when there are enough sales,
    else the guide price ("guide"). None when PriceCharting has neither."""
    guide = parse_prices(html)
    values: dict[str, dict | None] = {}

    for grade in GRADE_LABELS:
        recent = value_from_sales(parse_sales(html, grade), today)
        if recent is not None:
            values[grade] = {
                "value": recent[0],
                "basis": "recent_sales",
                "sale_count": recent[1],
                "guide": guide.get(grade),
            }
        elif guide.get(grade) is not None:
            values[grade] = {
                "value": guide[grade],
                "basis": "guide",
                "sale_count": None,
                "guide": guide[grade],
            }
        else:
            values[grade] = None

    return values


async def get_prices(url: str) -> dict[str, dict | None]:
    """Each grade's value from a card page (see parse_values)."""
    global _last_request_at

    wait = REQUEST_SPACING_SECONDS - (time.monotonic() - _last_request_at)
    if wait > 0:
        await asyncio.sleep(wait)

    try:
        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            response = await client.get(url)
    finally:
        _last_request_at = time.monotonic()

    if response.status_code in (403, 429):
        raise PriceChartingBlocked(
            f"PriceCharting refused the request (HTTP {response.status_code}); "
            "PSA prices were left as they were."
        )
    if response.status_code == 404:
        raise PriceChartingError("Page not found. Check the saved PriceCharting URL.")
    response.raise_for_status()

    return parse_values(response.text, date.today())
