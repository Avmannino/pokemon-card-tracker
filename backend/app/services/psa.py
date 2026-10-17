"""Photos of your own slabs, from PSA's official Public API.

One request per cert number, made only when you save a cert number. The
free plan allows 100 requests a day.
"""

import logging
import re
from typing import Any

import httpx

from ..config import settings


BASE_URL = "https://api.psacard.com/publicapi"

logger = logging.getLogger("psa")


class PSAError(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(settings.psa_api_token)


def clean_cert_number(value: str | None) -> str | None:
    """Digits only ("#12 345 678" -> "12345678"). None when empty; raises
    ValueError when it can't be a PSA cert number."""
    digits = re.sub(r"[\s#-]", "", value or "")
    if not digits:
        return None
    if not digits.isdigit() or not 4 <= len(digits) <= 12:
        raise ValueError("A PSA cert number is the 8 or 9 digits on the slab's label.")
    return digits


async def get_slab_images(cert_number: str) -> dict[str, Any]:
    """{"front": url | None, "back": url | None}. Both are None when PSA
    has no photos of that slab (common for older ones)."""
    if not is_configured():
        raise PSAError("PSA_API_TOKEN isn't set on the server.")

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            f"{BASE_URL}/cert/GetImagesByCertNumber/{cert_number}",
            headers={"Authorization": f"bearer {settings.psa_api_token}"},
        )

    said = response.text.strip().strip('"')[:200] or "no message"

    if response.status_code == 429:
        raise PSAError(f"PSA's daily limit is used up ({said}). Try again tomorrow.")
    if response.status_code in (401, 403, 500):
        # PSA reports a bad or expired token as a 500.
        logger.warning("PSA rejected cert %s: HTTP %s %s", cert_number, response.status_code, said)
        raise PSAError(
            f"PSA rejected the lookup (HTTP {response.status_code}: {said}). Check "
            "PSA_API_TOKEN in Render: only the token itself, no quotes or "
            "\"bearer\"."
        )
    response.raise_for_status()

    data = response.json()
    front = back = None

    for image in data if isinstance(data, list) else []:
        url = image.get("ImageURL") if isinstance(image, dict) else None
        if not url:
            continue
        if image.get("IsFrontImage"):
            front = front or url
        else:
            back = back or url

    if front is None and back is None:
        logger.info("No PSA photos for cert %s: %.300s", cert_number, response.text)

    return {"front": front, "back": back}
