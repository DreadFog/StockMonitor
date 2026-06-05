"""Metro online catalog image lookup and local caching.

The lookup is a best-effort two-step call to Metro France's public shop API:

1. Search by EAN to obtain the internal Betty article id.
2. Fetch the variant details to get the original ``imageUrl``.

The returned URL is stripped of all query-string filters so callers can request
it at any size (or download it once and serve it locally).

Cached images are stored under ``<instance_path>/product_images/<sha256>.<ext>``.
There is no expiration: Metro CDN URLs are content-addressed and stable.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from typing import Optional
from urllib.parse import urlparse

import requests
from flask import current_app

_METRO_SEARCH_URL = (
    "https://shop.metro.fr/searchdiscover/articlesearch/search"
    "?storeId=00033&language=fr-FR&country=FR&rows=48&page=1&facets=true&categories=true"
    "&query={ean}"
)
_METRO_VARIANT_URL = (
    "https://shop.metro.fr/evaluate.article.v1/betty-variants"
    "?storeIds=00033&country=FR&locale=fr-FR&ids={variant_id}"
)
_REQUEST_HEADERS = {
    "User-Agent": "StockMonitor/1.0 (+https://localhost) python-requests",
    "Accept": "application/json",
}
_REQUEST_TIMEOUT = 6  # seconds, per HTTP call
_DEFAULT_EXT = ".png"

_log = logging.getLogger(__name__)


def _strip_query(url: str) -> str:
    """Drop the query string and fragment from a URL."""
    return url.split("?", 1)[0].split("#", 1)[0]


def fetch_metro_image_url(ean: str) -> Optional[str]:
    """Resolve a Metro CDN image URL for the given EAN, or ``None`` on miss/error."""
    ean = (ean or "").strip()
    if not ean.isdigit() or len(ean) not in (8, 13, 14):
        return None

    try:
        search_resp = requests.get(
            _METRO_SEARCH_URL.format(ean=ean),
            headers=_REQUEST_HEADERS,
            timeout=_REQUEST_TIMEOUT,
        )
        search_resp.raise_for_status()
        search_data = search_resp.json()
    except (requests.RequestException, ValueError) as exc:
        _log.info("Metro search for EAN %s failed: %s", ean, exc)
        return None

    result_ids = search_data.get("resultIds") or []
    if not result_ids:
        return None
    variant_id = result_ids[0]

    try:
        variant_resp = requests.get(
            _METRO_VARIANT_URL.format(variant_id=variant_id),
            headers=_REQUEST_HEADERS,
            timeout=_REQUEST_TIMEOUT,
        )
        variant_resp.raise_for_status()
        variant_data = variant_resp.json()
    except (requests.RequestException, ValueError) as exc:
        _log.info("Metro variant lookup for %s failed: %s", variant_id, exc)
        return None

    result_map = variant_data.get("result") or {}
    # The result key is the article id (variant id minus the last 4 store digits).
    for article_payload in result_map.values():
        variants = article_payload.get("variants") or {}
        for variant_payload in variants.values():
            image_url = variant_payload.get("imageUrl")
            if image_url:
                return _strip_query(image_url)
    return None


# ---------------------------------------------------------------- cache helpers


def _cache_dir() -> str:
    base = os.path.join(current_app.instance_path, "product_images")
    os.makedirs(base, exist_ok=True)
    return base


def _safe_ext(url: str) -> str:
    path = urlparse(url).path
    _, ext = os.path.splitext(path)
    if not ext or not re.fullmatch(r"\.[A-Za-z0-9]{1,5}", ext):
        return _DEFAULT_EXT
    return ext.lower()


def cached_image_path(image_url: str) -> str:
    """Return the local filesystem path where the image for ``image_url`` is cached."""
    digest = hashlib.sha256(image_url.encode("utf-8")).hexdigest()
    return os.path.join(_cache_dir(), f"{digest}{_safe_ext(image_url)}")


def ensure_cached_image(image_url: str) -> Optional[str]:
    """Ensure the remote image is cached locally and return its path (or ``None``)."""
    if not image_url:
        return None
    path = cached_image_path(image_url)
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        return path
    try:
        resp = requests.get(image_url, headers=_REQUEST_HEADERS, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        _log.info("Failed to download cached image %s: %s", image_url, exc)
        return None
    tmp_path = f"{path}.part"
    try:
        with open(tmp_path, "wb") as handle:
            handle.write(resp.content)
        os.replace(tmp_path, path)
    except OSError as exc:
        _log.warning("Failed to write cached image %s: %s", path, exc)
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return None
    return path
