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


# The Grading Opportunities checker's prices for a card you don't own.
CHECKER_LABELS = {
    "RAW": "Ungraded",
    "PSA_9": "Grade 9",
    "PSA_10": "PSA 10",
}

SEARCH_URL = "https://www.pricecharting.com/search-products"


def _price(text: str) -> float | None:
    try:
        return float(text.replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


def parse_prices(html: str, labels: dict[str, str] = GRADE_LABELS) -> dict[str, float | None]:
    """{grade: price} from a card page's price guide, for `labels` (grade ->
    row name). A grade PriceCharting shows as "-" (no sales) is None."""
    start = html.find('id="full-prices"')
    if start == -1:
        raise PriceChartingError("No price guide found on the page.")

    section = html[start:]
    prices: dict[str, float | None] = {}
    found = 0

    for grade, label in labels.items():
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


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)).strip()


def parse_card_page(html: str, url: str) -> dict:
    """A card page's name, set, image and raw / PSA 9 / PSA 10 prices."""
    heading = re.search(r'<h1[^>]*id="product_name"[^>]*>(.*?)</h1>', html, re.S)
    name_part, _, set_part = (heading.group(1) if heading else "").partition("<a")
    image = re.search(
        r"https://storage\.googleapis\.com/images\.pricecharting\.com/[a-f0-9]+/240\.jpg", html
    )
    return {
        "url": url,
        "name": _text(name_part) or None,
        "set_name": _text("<a" + set_part) or None,
        "image_url": image.group(0) if image else None,
        "prices": parse_prices(html, CHECKER_LABELS),
    }


def parse_search_results(html: str, limit: int = 12) -> list[dict]:
    """The cards on a PriceCharting search results page."""
    results = []
    for row in re.findall(r'<tr[^>]*id="product-\d+"[^>]*>(.*?)</tr>', html, re.S):
        link = re.search(
            r'<td class="title">\s*<a href="(https://www\.pricecharting\.com/game/[^"]+)"[^>]*>(.*?)</a>',
            row, re.S,
        )
        if not link:
            continue
        set_name = re.search(r'<div class="console-in-title">\s*<a[^>]*>(.*?)</a>', row, re.S)
        image = re.search(r'<img class="photo"[^>]*src="([^"]+)"', row)
        raw = re.search(r'used_price">\s*<span class="js-price">([^<]*)</span>', row)
        results.append({
            "url": page_url(link.group(1)),
            "name": _text(link.group(2)),
            "set_name": _text(set_name.group(1)) if set_name else None,
            "image_url": image.group(1) if image else None,
            "raw_price": _price(raw.group(1)) if raw else None,
        })
        if len(results) >= limit:
            break
    return results


async def _fetch(url: str, params: dict | None = None) -> httpx.Response:
    """One PriceCharting page, spaced from the last request."""
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
            response = await client.get(url, params=params)
    finally:
        _last_request_at = time.monotonic()

    if response.status_code in (403, 429):
        raise PriceChartingBlocked(
            f"PriceCharting refused the request (HTTP {response.status_code})."
        )
    if response.status_code == 404:
        raise PriceChartingError("Page not found. Check the PriceCharting URL.")
    response.raise_for_status()
    return response


async def search(query: str) -> list[dict]:
    """Cards matching `query` on PriceCharting. A search with one match
    lands straight on its card page."""
    response = await _fetch(SEARCH_URL, {"type": "prices", "q": query})
    landed = page_url(str(response.url))
    if landed:
        page = parse_card_page(response.text, landed)
        return [{
            "url": landed,
            "name": page["name"],
            "set_name": page["set_name"],
            "image_url": page["image_url"],
            "raw_price": page["prices"].get("RAW"),
        }]
    return parse_search_results(response.text)


async def get_card(url: str) -> dict:
    """A card page for the grading checker (see parse_card_page)."""
    canonical = page_url(url)
    if canonical is None:
        raise PriceChartingError("That isn't a PriceCharting card page.")
    response = await _fetch(canonical)
    return parse_card_page(response.text, canonical)


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
