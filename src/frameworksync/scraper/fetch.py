"""Download a single documentation page.

This file only knows how to fetch a URL reliably. It does not know about
libraries, folders, or saving; run_scraper.py handles that.
"""

import time

import httpx

HEADERS = {"User-Agent": "FrameworkSync/0.1 (learning project; docs scraper)"}
TIMEOUT_SECONDS = 20
MAX_RETRIES = 3


class FetchError(Exception):
    """Raised when a page cannot be downloaded or looks wrong."""


def fetch_text(url: str) -> str:
    """Return the text of `url`, retrying on temporary failures."""
    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = httpx.get(
                url,
                headers=HEADERS,
                timeout=TIMEOUT_SECONDS,
                follow_redirects=True,
            )
            response.raise_for_status()
            return _validate(url, response.text)

        except httpx.HTTPStatusError as err:
            status = err.response.status_code
            # 4xx means the request itself is wrong (e.g. 404 bad URL).
            # Retrying can't fix that, except 429 = "slow down".
            if status < 500 and status != 429:
                raise FetchError(f"{url} returned HTTP {status}") from err
            last_error = err

        except httpx.TransportError as err:
            # Network down, DNS failure, timeout... worth retrying.
            last_error = err

        if attempt < MAX_RETRIES:
            time.sleep(2**attempt)  # wait 2s, then 4s

    raise FetchError(f"Gave up on {url} after {MAX_RETRIES} attempts: {last_error}")


def _validate(url: str, text: str) -> str:
    """Reject empty pages and HTML pages (we asked for Markdown)."""
    stripped = text.lstrip()
    if not stripped:
        raise FetchError(f"{url} returned an empty page")
    if stripped[:15].lower().startswith(("<!doctype", "<html")):
        raise FetchError(f"{url} returned HTML, not Markdown (wrong URL or error page?)")
    return text