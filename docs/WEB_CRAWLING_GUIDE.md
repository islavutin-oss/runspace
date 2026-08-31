# Web crawling — shared pattern for agentino agents

The canonical helper is `helpers.web_crawler` (in the runspace flat
package — same import shape as `helpers.session`, `helpers.messaging`,
etc.). Use it instead of rolling your own httpx + playwright dance in
every project. This doc covers when to crawl, how to do it cheaply,
and the ethical guardrails.

## When to crawl

Three legitimate use cases come up in the ecosystem:

1. **Backfill structured public data** — e.g. a catalogue app pulls
   `og:image` from a product's canonical page to enrich the
   catalog. The data is already public; we cache it locally so we
   don't re-hit the source on every request.
2. **Discovery beyond a curated dataset** — e.g. KYB cross-checks
   officer LinkedIn URLs found on a company's leadership page against
   declared director names.
3. **Verification** — e.g. checking whether a perfume / company is
   referenced on a known authoritative site before trusting an
   agent's claim.

Bad use cases — don't:

- Bulk-scraping a competitor's catalog to clone it.
- Repeatedly fetching pages that change rarely (cache them).
- Hitting any site at > 1 req/s without explicit permission.
- Crawling pages explicitly disallowed by robots.txt.

## The three-tier pattern

The KYB platform proved this pattern in production. All agents
should follow it:

```python
from runspace.helpers.web_crawler import fetch_html, extract_og_image, fetch_image_bytes

html = fetch_html(url)                  # httpx, with stealth fallback
img_url = extract_og_image(html, url)   # og:image (or twitter:image)
img = fetch_image_bytes(img_url)        # validated bytes + content-type
```

Layer 1 — **plain httpx** with a realistic desktop UA. Fast, cheap.
Works on most sites.

Layer 2 — **Playwright + playwright-stealth** with real Chromium.
Defeats UA-based 403s (Fragrantica, LinkedIn, Cloudflare). Much
heavier (~2-5s per page). Use only when layer 1 fails — which the
default `fetch_html()` handles automatically.

Layer 3 — **process-local throttle**. `polite_throttle()` enforces a
~1.2s minimum interval between any two fetches in the same process,
so concurrent agents don't accidentally burst a target site.

## API reference

```python
from runspace.helpers.web_crawler import (
    fetch_html,              # canonical fetch — auto httpx + stealth fallback
    fetch_html_httpx,        # plain HTTP only
    fetch_html_stealth,      # real Chromium only
    fetch_image_bytes,       # validated image download → (bytes, content_type)
    extract_og_image,        # og:image / twitter:image meta extractor
    polite_throttle,         # process-local rate limiter
    DEFAULT_UA,              # Chrome desktop UA string
)
```

### Common recipe — backfill an `og:image`

```python
from pathlib import Path
from runspace.helpers.web_crawler import fetch_html, extract_og_image, fetch_image_bytes

def backfill_image(page_url: str, save_to: Path) -> bool:
    html = fetch_html(page_url)
    if not html: return False
    img_url = extract_og_image(html, page_url)
    if not img_url: return False
    result = fetch_image_bytes(img_url)
    if not result: return False
    data, _ct = result
    save_to.write_bytes(data)
    return True
```

### Stealth-only when you know layer 1 will fail

```python
from runspace.helpers.web_crawler import fetch_html

# Fragrantica, LinkedIn, etc. — known to 403 plain HTTP
html = fetch_html(linkedin_url, prefer="stealth")
```

### Custom throttle interval

```python
from runspace.helpers.web_crawler import polite_throttle, fetch_html_httpx

# Each call sleeps to maintain 2s minimum between fetches.
for url in urls:
    polite_throttle(min_interval=2.0)
    html = fetch_html_httpx(url)
```

## Ethical & legal guardrails

- **Identify yourself when bulk-crawling.** For one-off backfills,
  prefer using a UA that mentions your project (see KYB's User-Agent
  comment block). The shared helpers use a standard Chrome UA by
  default; if you're doing > 100 requests in one run, consider
  swapping in a project-identifying UA.
- **Don't bypass paywalls.** If a site explicitly gates content, don't
  use stealth to get around it. Stealth is for UA-based bot blocks,
  not for circumventing access controls.
- **Cache locally.** Hit each URL once, store the result. Re-running
  the same crawl multiple times to refresh "live" data is a code
  smell — set up a proper update routine instead.
- **Respect robots.txt for repeated crawls.** One-off backfills are
  generally fine; a recurring routine should honour robots.txt.
- **Image rights.** `og:image` is brand-supplied metadata and is
  typically OK to display with attribution as a link preview / card
  thumbnail (every social network does this). Don't republish images
  as if they were your own.

## Existing in-tree examples

- **Counterparty checks** — a tool that fetches a company's leadership
  page and extracts the named officers
  (`_fetch_with_playwright`) — the original implementation this helper
  was extracted from. Discovers + fetches corp leadership pages.
- **Catalogue enrichment** — a backfill script that pulls `og:image` from a
  product page to fill in a catalogue entry's picture.

## Installation

The Python side:

```bash
pip install playwright playwright-stealth httpx
playwright install chromium
```

If you don't have Chromium installed, `fetch_html_stealth` falls back
to logging the failure and returning `None` — the caller's other
layers still work.
