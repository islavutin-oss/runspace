"""Web-crawl primitives shared by agentino-ecosystem agents."""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from urllib.parse import urljoin

import httpx

log = logging.getLogger("runspace.web_crawler")

# Realistic desktop Chrome UA. Tracking the latest stable major is
# fine — sites generally accept "recent Chrome" without caring about
# the exact build.
DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Process-wide throttle lock. Shared across all callers in this process
# so an agent making three back-to-back fetches doesn't accidentally
# burst — the throttle is meant to be the floor, not per-callsite.
_THROTTLE_LOCK = threading.Lock()
_LAST_CALL_TS = 0.0


def polite_throttle(min_interval: float = 1.2) -> None:
    """Block until at least `min_interval` seconds have elapsed since
    the previous polite_throttle() call. Process-local."""
    global _LAST_CALL_TS
    with _THROTTLE_LOCK:
        delta = time.monotonic() - _LAST_CALL_TS
        if delta < min_interval:
            time.sleep(min_interval - delta)
        _LAST_CALL_TS = time.monotonic()


_OG_RE = re.compile(
    r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
    re.I,
)
_TW_RE = re.compile(
    r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.I,
)


def extract_og_image(html: str, base_url: str = "") -> str | None:
    """Pull the page's og:image URL (preferred) or twitter:image
    (fallback). Resolves relative paths against `base_url` so callers
    receive an absolute URL they can fetch immediately."""
    if not html:
        return None
    for pat in (_OG_RE, _TW_RE):
        m = pat.search(html)
        if m:
            url = m.group(1).strip()
            return urljoin(base_url, url) if base_url else url
    return None


def fetch_html_httpx(
    url: str,
    timeout: float = 12,
    headers: dict[str, str] | None = None,
) -> str | None:
    """Plain HTTP GET with a realistic browser UA. Returns response
    text on 200, None otherwise. Throttled via polite_throttle()."""
    polite_throttle()
    h = {"User-Agent": DEFAULT_UA, "Accept-Language": "en-US,en;q=0.9"}
    if headers:
        h.update(headers)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=h) as c:
            r = c.get(url)
            if r.status_code == 200 and r.text:
                return r.text
            log.info("httpx %s → %d", url, r.status_code)
            return None
    except httpx.HTTPError as exc:
        log.info("httpx %s → %r", url, exc)
        return None


def fetch_html_stealth(
    url: str,
    timeout_ms: int = 25000,
    wait_networkidle_ms: int = 8000,
    cloudflare_wait_ms: int = 8000,
) -> str | None:
    """Headless Chromium + playwright-stealth. Defeats UA-based 403s.
    Returns full DOM HTML or None on any failure.

    Heavier than httpx (Chromium startup ~300-700ms, page render
    ~2-5s, plus another ~3-8s when a site sits behind Cloudflare's
    JS challenge). Use only when plain HTTP is blocked —
    `fetch_html()` auto-falls-back so most callers don't need to
    choose.

    Cloudflare handling: when the page title is `"Just a moment..."`
    we sleep up to `cloudflare_wait_ms` and re-poll. The challenge
    typically auto-resolves in 3-6s with a stealth Chromium. After
    that, if the title is still the challenge string, give up — we
    can't pass that site without a proxy / paid solver."""
    try:
        from playwright.sync_api import sync_playwright
        from playwright_stealth import Stealth
    except ImportError as exc:
        log.info("stealth unavailable: %r", exc)
        return None
    polite_throttle()
    try:
        with Stealth().use_sync(sync_playwright()) as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(
                    user_agent=DEFAULT_UA,
                    viewport={"width": 1280, "height": 800},
                    locale="en-US",
                )
                page = ctx.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                # SPAs often hydrate after DOMContentLoaded; give them a beat.
                try:
                    page.wait_for_load_state("networkidle", timeout=wait_networkidle_ms)
                except Exception:
                    pass

                # Cloudflare "Just a moment..." → wait for the auto-redirect
                # away from the interstitial. Poll the title every 500ms.
                deadline = time.monotonic() + (cloudflare_wait_ms / 1000.0)
                while time.monotonic() < deadline:
                    title = (page.title() or "").strip()
                    if "Just a moment" not in title and "Attention Required" not in title:
                        break
                    time.sleep(0.5)
                return page.content()
            finally:
                browser.close()
    except Exception as exc:
        log.info("stealth %s → %r", url, exc)
        return None


# Status codes that signal "try stealth instead" — anti-bot responses
# typically 403, sometimes 429 or empty 200 (no body).
_BLOCKED_STATUS = (401, 403, 429)


def fetch_html(url: str, prefer: str = "auto") -> str | None:
    """Canonical fetch entry point.

      - prefer="auto" (default): httpx first; on 4xx-block / empty
        body, fall back to stealth Chromium.
      - prefer="httpx":           httpx only.
      - prefer="stealth":         skip httpx, go straight to stealth.

    Returns HTML string or None.
    """
    if prefer == "stealth":
        return fetch_html_stealth(url)
    html = fetch_html_httpx(url)
    if html or prefer == "httpx":
        return html
    # Plain HTTP returned nothing — try stealth.
    return fetch_html_stealth(url)


_BRAVE_IMG_URL = "https://api.search.brave.com/res/v1/images/search"


def brave_image_search(query: str, count: int = 5, country: str = "us") -> list[dict]:
    """Brave Image Search — returns ranked image URLs from the public
    web. Use this when a target site blocks direct crawling (Cloudflare
    challenge / 403) but you can name the resource and trust Brave to
    have indexed it.

    Reads `BRAVE_API_KEY` from env. Returns [] on missing key or any
    HTTP error so callers degrade gracefully.

    Each result dict: {url, source, title, thumbnail}. `url` is the
    raw image URL; `source` is the page that hosts it (useful for
    attribution / sanity-checking).

    Free tier: 2000 queries/month, 1 qps — we honour `polite_throttle`."""
    key = os.environ.get("BRAVE_API_KEY", "")
    if not key:
        log.info("brave_image_search: BRAVE_API_KEY not set")
        return []
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": key,
    }
    params = {
        "q": query,
        "count": min(count, 20),
        "country": country.lower(),
        "safesearch": "off",
    }
    polite_throttle()
    try:
        with httpx.Client(timeout=12) as c:
            r = c.get(_BRAVE_IMG_URL, headers=headers, params=params)
            if r.status_code == 429:
                time.sleep(2.0)
                r = c.get(_BRAVE_IMG_URL, headers=headers, params=params)
            if r.status_code != 200:
                log.info("brave_image_search %s → %d", query, r.status_code)
                return []
            data = r.json()
    except httpx.HTTPError as exc:
        log.info("brave_image_search %s → %r", query, exc)
        return []

    results = data.get("results", []) or []
    out = []
    for r in results:
        props = r.get("properties", {}) or {}
        url = (
            props.get("url") or r.get("thumbnail", {}).get("src")
            if isinstance(r.get("thumbnail"), dict)
            else None
        )
        out.append(
            {
                "url": url or r.get("url"),
                "source": r.get("url"),  # page the image lives on
                "title": r.get("title") or "",
                "thumbnail": (r.get("thumbnail") or {}).get("src")
                if isinstance(r.get("thumbnail"), dict)
                else None,
            }
        )
    return [o for o in out if o["url"]]


def fetch_image_bytes(
    url: str,
    timeout: float = 20,
    min_bytes: int = 1024,
    max_bytes: int = 8 * 1024 * 1024,
) -> tuple[bytes, str] | None:
    """Download an image. Returns (bytes, content_type) or None.

    Validates: HTTP 200, content-type starts with `image/`, and body
    size within [min_bytes, max_bytes] (defaults: 1 KB .. 8 MB)."""
    polite_throttle()
    try:
        with httpx.Client(
            timeout=timeout, follow_redirects=True, headers={"User-Agent": DEFAULT_UA}
        ) as c:
            r = c.get(url)
            if r.status_code != 200:
                log.info("image %s → %d", url, r.status_code)
                return None
            ct = r.headers.get("content-type", "")
            if not ct.startswith("image/"):
                log.info("image %s → ct=%s (not image)", url, ct)
                return None
            data = r.content
            if not (min_bytes <= len(data) <= max_bytes):
                log.info("image %s → size %d outside bounds", url, len(data))
                return None
            return data, ct
    except httpx.HTTPError as exc:
        log.info("image %s → %r", url, exc)
        return None
