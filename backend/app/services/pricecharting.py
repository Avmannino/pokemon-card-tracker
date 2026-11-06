"""PSA prices re-read from the PriceCharting page you linked a price to.

Only pages you saved as a PSA value's source URL are read, one request per
page, and only for the grade you own. Prices come from the page's "Full
Price Guide" table. PriceCharting's Grade 8 and Grade 9 rows cover every
grading company; its PSA 10 row is PSA only.
"""

import asyncio
import re
import time
from urllib.parse import urlsplit, urlunsplit

import httpx


# App grade -> PriceCharting's price guide row.
GRADE_LABELS = {
    "PSA_8": "Grade 8",
    "PSA_9": "Grade 9",
    "PSA_10": "PSA 10",
}

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


async def get_prices(url: str) -> dict[str, float | None]:
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

    return parse_prices(response.text)
