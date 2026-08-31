# Vision capabilities on runspace apps

How to use multimodal input (image / scanned PDF page) on apps that reach
a model through an OpenAI-compatible gateway.

## What works

- **Codex via the gateway** — `gpt-5.4-codex` accepts `input_image` in
  the Responses API. Quality on printed Cyprus/Greek/EN invoices is
  strong (supplier name, IBAN, totals, line items, dates) — verified
  on real Telegram-forwarded scans 2026-04-30.
- **Anthropic direct** — Claude Sonnet 4.5 / Haiku 4.5 also work for
  vision via the Messages API. Use it as a fallback when Codex hits
  a daily-cap or for layouts that need stronger reasoning over noise.

## Required request shape (Codex via the gateway)

Endpoint: `POST {base_url}/codex/responses` — note the `/codex/` prefix.
Streaming is **mandatory** — the relay returns `400 Stream must be set
to true` otherwise.

```python
import base64, httpx, io, json, os
from PIL import Image

# 1. Resize. Full-res >>1024px → 413 Payload Too Large at the relay.
img = Image.open(scan_path)
img.thumbnail((1024, 1024))      # keep aspect; 1024 is the sweet spot
buf = io.BytesIO()
img.save(buf, format="JPEG", quality=80)
img_b64 = base64.b64encode(buf.getvalue()).decode()

body = {
    "model": "gpt-5.4-codex",
    "instructions": "...system prompt...",
    "input": [{
        "role": "user",
        "content": [
            {"type": "input_text",
             "text": "Extract: supplier, total, currency, due_date, iban. JSON only."},
            {"type": "input_image",
             "image_url": f"data:image/jpeg;base64,{img_b64}"},
        ]
    }],
    "store": False,
    "stream": True,             # ← required by the gateway
}

text = ""
with httpx.stream(
    "POST", f"{os.environ['AI_BASE_URL']}/codex/responses",
    json=body,
    headers={"Authorization": f"Bearer {os.environ['AI_API_KEY']}"},
    timeout=180,
) as r:
    for line in r.iter_lines():
        if not line.startswith("data: "):
            continue
        try:
            chunk = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        if chunk.get("type") == "response.output_text.delta":
            text += chunk.get("delta", "")

# `text` is the LLM's answer concatenated from streaming deltas.
```

## Inside an agentino tool

If you're building a `@tool` that needs vision, two layers:

1. A `vision.py` module in `api/` does the raw HTTP streaming above —
   a single function `extract_invoice(image_path: Path) -> dict` returning
   parsed JSON.
2. The tool just calls it and returns the dict to the LLM:

```python
# agents/<agent>/tools/process_invoice.py
from agentino import tool
from myapp import store, vision  # your own module, not runspace's

@tool
async def process_invoice(inbox_id: str) -> dict:
    """Run vision OCR on an inbox attachment and create an invoice record."""
    item = store.get_inbox_item(inbox_id)
    if not item or not item.get("file"):
        return {"error": "no attachment to process"}
    extracted = await vision.extract_invoice(item["file"])
    inv_id = store.next_invoice_id()
    invoice = {
        "id": inv_id,
        "supplier": extracted.get("supplier"),
        "amount": extracted.get("total_amount"),
        "currency": extracted.get("currency"),
        "due_date": extracted.get("due_date") or _infer_due_date(item.get("caption")),
        "iban": extracted.get("iban"),
        "raw": extracted,
        "source_inbox_id": inbox_id,
        "review_required": _confidence_low(extracted),
        "created_at": store.now_iso(),
    }
    store.save_invoice(invoice)
    store.update_inbox_item(inbox_id, status="processed", invoice_id=inv_id)
    return {"ok": True, "invoice_id": inv_id, "supplier": invoice["supplier"]}
```

Tool returns plain dict; the LLM never sees the base64 blob. Keeps the
tool-call payload small and the LLM focused on the structured fields.

## Inputs from PDFs

PDFs are not images — they have to be rasterized first. Standard tool
is `pdftoppm` (poppler) → PIL → JPEG → base64.

```python
import subprocess, tempfile
from pathlib import Path
from PIL import Image

def first_page_jpeg(pdf_path: Path) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        # -r 150 = 150 DPI; first page only with -f 1 -l 1
        subprocess.run(["pdftoppm", "-r", "150", "-f", "1", "-l", "1",
                        str(pdf_path), f"{tmp}/p"], check=True)
        ppm = next(Path(tmp).glob("p-*.ppm"))
        img = Image.open(ppm)
        img.thumbnail((1024, 1024))
        out = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        img.save(out.name, format="JPEG", quality=80)
        return Path(out.name).read_bytes()
```

For multi-page invoices, loop pages and either send each separately
(easier, parallelizable) or stitch first-N as a tall image.

## Response quality observations

Tested on the maintainer's real Telegram-forwarded invoices (Cyprus EUR,
mixed languages):

- **Strong**: supplier name, total amount, currency, invoice number,
  IBAN/SWIFT, dates printed on the document.
- **Weak**: due dates that are NOT on the invoice (Cyprus B2B usually
  prints "net 14" or nothing). Always fall back to the caption text
  ("Due 29.04", "Payment due 10/05") or the platform's default rule.
- **Weak**: handwritten quantity overrides on otherwise-printed lines.
  Flag `review_required` when confidence is uncertain.

## Failure modes & retries

| symptom                                   | fix                                                                  |
|-------------------------------------------|----------------------------------------------------------------------|
| `413 Payload Too Large`                   | thumbnail to ≤1024 before base64                                     |
| `400 Stream must be set to true`          | add `"stream": True`                                                 |
| `Invalid value: 'tool'` on tool round-trip | known agentino-codex quirk — use agentino's `Agent` class, not raw HTTP |
| empty response after tool call            | almost always invalid JSON Schema in `tool.parameters` — annotate `list[dict]` not bare `list` |
| nonsense extraction                       | scan is rotated / upside-down. Pre-rotate via PIL before sending.    |

## Cost / latency rough numbers

- 1024×1024 JPEG @ q80 ≈ 110 KB base64 → ~0.5–2 s wall-time per scan
  through the gateway (P50 ≈ 1.2 s).
- One-page invoice extraction averages 2–4k input tokens, 200–500
  output tokens, so throughput is usually bounded by your endpoint's rate
  limit rather than by token cost.

## When to use Anthropic instead

Switch to `claude-sonnet-4-5` (direct API, not via the gateway) when:

- Codex daily cap hits (ChatGPT-Plus subscription quota).
- Text orientation is non-trivial (rotated, multi-column, dense tables).
- You need the model to reason about the layout ("which column is the
  unit price vs. the line total?") — Sonnet handles ambiguous layouts
  more confidently.

The wrapper is similar — `messages.create` with an `image` content block,
and no streaming requirement.
