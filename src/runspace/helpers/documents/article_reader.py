"""Article tools — read and query PDFs and web articles."""

from __future__ import annotations

import os
import re
from pathlib import Path

try:
    from agentino.core.tool import tool
except ImportError:
    # The agent runtime is an optional extra; without it these stay plain
    # functions, which is what a non-agentino host wants anyway.
    def tool(fn):  # type: ignore[misc]
        return fn

# ---------------------------------------------------------------------------
# Article cache — stores full text per source, separate from conversation
# ---------------------------------------------------------------------------

_article_cache: dict[str, dict] = {}  # source → {text, title, pages, chars}


def _ensure_pypdf():
    try:
        import pypdf

        return pypdf
    except ImportError:
        raise ImportError("pypdf is required for PDF reading. Install: pip install pypdf")


def _download(url: str, timeout: int = 60) -> bytes:
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "ag-agent/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _extract_pdf_text(data: bytes, max_pages: int = 100) -> str:
    pypdf = _ensure_pypdf()
    import io

    reader = pypdf.PdfReader(io.BytesIO(data))
    pages = []
    for i, page in enumerate(reader.pages):
        if i >= max_pages:
            break
        text = page.extract_text() or ""
        if text.strip():
            pages.append(text)
    return "\n\n".join(pages)


def _extract_html_text(html: str) -> str:
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:100000]


def _make_summary(text: str, source: str) -> str:
    """Create a brief summary from the first ~3000 chars of the article."""
    # Extract title (first non-empty line)
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    title = lines[0][:200] if lines else "Unknown"

    # Try to find abstract
    abstract = ""
    text_lower = text.lower()
    for marker in ["abstract", "summary", "overview"]:
        idx = text_lower.find(marker)
        if idx != -1:
            # Grab text after the marker
            chunk = text[idx : idx + 1500]
            # Take until double newline or end
            end = chunk.find("\n\n", len(marker) + 1)
            abstract = chunk[len(marker) : end if end > 0 else len(chunk)].strip()
            # Clean up
            abstract = re.sub(r"^\W+", "", abstract)
            break

    if not abstract:
        abstract = " ".join(text.split()[:200])

    # Extract section headings
    headings = []
    for line in lines[:200]:
        if (
            len(line) < 100
            and (line[0].isdigit() or line.isupper() or line.startswith("#"))
            and not line.startswith("http")
        ):
            headings.append(line)
        if len(headings) >= 15:
            break

    parts = [
        f"Title: {title}",
        f"Source: {source}",
        f"Size: {len(text):,} chars cached",
        "",
        f"Abstract: {abstract[:800]}",
    ]
    if headings:
        parts.append(f"\nSections: {', '.join(headings[:10])}")
    parts.append(
        f'\nQuery this article with `query_article("{source}", "<question>")`.'
        f"\nMANDATORY: Make at least 3-5 queries to extract all implementation details:"
        f"\n  1. Core algorithm / architecture"
        f"\n  2. Data structures and formats"
        f"\n  3. Key parameters, thresholds, hyperparameters"
        f"\n  4. Benchmarks, evaluation methodology"
        f"\n  5. Edge cases, limitations, gotchas"
        f"\nIf a query returns nothing useful, rephrase with different terms."
    )
    return "\n".join(parts)


def _search_text(text: str, query: str, context_chars: int = 3000) -> str:
    """Search article text for relevant sections matching the query.

    Splits text into overlapping chunks, scores by keyword overlap,
    returns the top-scoring chunks. Works with PDFs (line-wrapped)
    and HTML (paragraph-based).
    """
    query_words = [w for w in query.lower().split() if len(w) > 2]
    if not query_words:
        return text[:context_chars]

    # Split into chunks of ~500 chars with 100-char overlap
    chunk_size = 500
    overlap = 100
    chunks = []
    for i in range(0, len(text), chunk_size - overlap):
        chunk = text[i : i + chunk_size]
        if chunk.strip():
            chunks.append((i, chunk))

    # Score each chunk by keyword hits
    scored = []
    for pos, chunk in chunks:
        chunk_lower = chunk.lower()
        hits = sum(1 for w in query_words if w in chunk_lower)
        if hits > 0:
            scored.append((hits, pos, chunk))

    if not scored:
        # Fallback: find any keyword and return surrounding context
        for word in sorted(query_words, key=len, reverse=True):
            idx = text.lower().find(word)
            if idx >= 0:
                start = max(0, idx - context_chars // 2)
                end = min(len(text), idx + context_chars // 2)
                return text[start:end]
        return "No relevant sections found for this query."

    # Return top matches, expanding each to include surrounding context
    scored.sort(key=lambda x: x[0], reverse=True)
    results = []
    total = 0
    seen_positions = set()
    for hits, pos, chunk in scored[:10]:
        # Avoid overlapping results
        if any(abs(pos - sp) < chunk_size for sp in seen_positions):
            continue
        seen_positions.add(pos)
        # Expand chunk to include surrounding lines
        start = max(0, pos - 200)
        end = min(len(text), pos + chunk_size + 200)
        expanded = text[start:end].strip()
        if total + len(expanded) > context_chars:
            break
        results.append(expanded)
        total += len(expanded)

    return "\n\n---\n\n".join(results)


@tool
def read_article(source: str) -> str:
    """Download and cache a PDF or web article for later querying.

    Returns a brief summary (title, abstract, sections). The full text
    is cached internally — use `query_article` to get specific details.

    This keeps the conversation context small while giving full access
    to the article content via targeted queries.

    Args:
        source: Local file path or URL (PDF or web page)

    Returns:
        Brief summary with instructions to query for details
    """
    source = source.strip()

    # Return cached summary if already loaded
    if source in _article_cache:
        cached = _article_cache[source]
        return f"[Already cached: {source}, {cached['chars']:,} chars]\n\n{cached['summary']}"

    # --- Load the article ---
    text = ""
    source_type = ""

    if not source.startswith(("http://", "https://")):
        # Local file
        path = Path(source).expanduser()
        if not path.exists():
            project_dir = os.environ.get("AGENTINO_PROJECT_DIR", "")
            if project_dir:
                alt = Path(project_dir) / source
                if alt.exists():
                    path = alt
        if not path.exists():
            return f"Error: file not found: {source}"

        if path.suffix.lower() == ".pdf":
            text = _extract_pdf_text(path.read_bytes())
            source_type = "PDF"
        else:
            text = path.read_text(encoding="utf-8", errors="replace")[:100000]
            source_type = "file"
    else:
        # URL
        url = source
        arxiv_match = re.match(r"https?://arxiv\.org/abs/(\d+\.\d+)", url)
        if arxiv_match:
            url = f"https://arxiv.org/pdf/{arxiv_match.group(1)}.pdf"

        try:
            data = _download(url)
        except Exception as e:
            return f"Error downloading {url}: {e}"

        if url.lower().endswith(".pdf") or data[:5] == b"%PDF-":
            text = _extract_pdf_text(data)
            source_type = "PDF"
        else:
            text = _extract_html_text(data.decode("utf-8", errors="replace"))
            source_type = "web"

    if not text.strip():
        return f"Error: no text extracted from {source}"

    # Cache the full text
    summary = _make_summary(text, source)
    _article_cache[source] = {
        "text": text,
        "summary": summary,
        "chars": len(text),
        "type": source_type,
    }

    return f"[Cached {source_type}: {source}, {len(text):,} chars]\n\n{summary}"


@tool
def query_article(source: str, question: str) -> str:
    """Query a cached article for specific information.

    Use after `read_article` to get details about specific topics,
    algorithms, parameters, or implementation details from the article.

    Args:
        source: The same source string used in read_article
        question: What you want to know (e.g., "window cache algorithm",
                  "data structures used", "benchmark results")

    Returns:
        Relevant sections from the article matching the question
    """
    if source not in _article_cache:
        return f'Article not cached. Call `read_article("{source}")` first.'

    text = _article_cache[source]["text"]
    results = _search_text(text, question)
    return f'[Query: "{question}" in {source}]\n\n{results}'
