"""White-label branded PDF generation — markdown → HTML → PDF."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

try:
    from agentino.core.tool import tool
except ImportError:
    # Standalone mode — define a no-op decorator
    def tool(fn):  # type: ignore
        return fn


log = logging.getLogger(__name__)


@dataclass
class BrandConfig:
    """White-label branding for PDF reports."""

    company: str = "Company"
    tagline: str = ""  # e.g. "example.com · Reg. 12345678"
    accent: str = "#191c1f"  # accent color for header bar + table headers
    logo_html: str = ""  # optional: inline HTML for logo (e.g. <img> or emoji)
    footer: str = ""  # custom footer text; empty = auto-generated
    address: str = ""  # company address for footer


def _make_style(brand: BrandConfig) -> str:
    """Generate CSS with brand accent color."""
    accent = brand.accent
    # Compute a light tint for table headers (accent + 90% white)
    f"{accent}18"  # ~10% opacity as hex suffix for bg
    return f"""
<style>
  @page {{ size: A4; margin: 20mm 18mm; }}
  body {{
    font-family: -apple-system, 'Helvetica Neue', Arial, sans-serif;
    color: #191c1f; font-size: 9.5pt; line-height: 1.5;
  }}
  .header {{
    display: flex; justify-content: space-between; align-items: flex-start;
    padding-bottom: 14px; margin-bottom: 20px;
    border-bottom: 2px solid {accent};
  }}
  .header h1 {{ font-size: 16pt; font-weight: 600; margin: 0; color: {accent}; }}
  .header .sub {{ font-size: 9pt; color: #6b7280; margin-top: 3px; }}
  .header .right {{ text-align: right; font-size: 8.5pt; color: #6b7280; }}
  .header .company {{ font-size: 10pt; font-weight: 600; color: {accent}; }}
  .header .logo {{ margin-bottom: 4px; }}
  h2 {{
    font-size: 11pt; font-weight: 600; margin: 20px 0 8px;
    padding-bottom: 4px; border-bottom: 1px solid #e0e0e0; color: {accent};
  }}
  h3 {{ font-size: 10pt; font-weight: 600; margin: 14px 0 6px; }}
  p, li {{ font-size: 9.5pt; margin: 4px 0; }}
  strong {{ font-weight: 600; }}
  table {{
    width: 100%; border-collapse: collapse; margin: 8px 0; font-size: 8.5pt;
  }}
  th {{
    background: {accent}; color: white; padding: 6px 8px; text-align: left;
    font-weight: 600; font-size: 7.5pt; text-transform: uppercase;
    letter-spacing: 0.03em;
  }}
  td {{ padding: 5px 8px; border-bottom: 1px solid #f0f0f0; color: #374151; }}
  tr:nth-child(even) {{ background: #fafafa; }}
  .kpi-row {{ display: flex; gap: 16px; margin: 12px 0; }}
  .kpi-card {{
    flex: 1; padding: 12px 16px; border-radius: 6px;
    border: 1px solid #e0e0e0; background: #fafafa;
  }}
  .kpi-card .label {{ font-size: 7.5pt; color: #6b7280; text-transform: uppercase; letter-spacing: 0.05em; }}
  .kpi-card .value {{ font-size: 16pt; font-weight: 700; color: {accent}; margin-top: 2px; }}
  .kpi-card .delta {{ font-size: 8pt; margin-top: 2px; }}
  .kpi-card .delta.up {{ color: #16a34a; }}
  .kpi-card .delta.down {{ color: #dc2626; }}
  .note {{
    background: #f9fafb; border: 1px solid #e0e0e0; border-radius: 4px;
    padding: 8px 12px; margin: 8px 0; font-size: 8.5pt; color: #6b7280;
  }}
  .footer {{
    margin-top: 24px; padding-top: 8px; border-top: 2px solid {accent};
    font-size: 7.5pt; color: #9ca3af; text-align: center;
  }}
  code {{ background: #f5f5f5; padding: 1px 4px; border-radius: 3px; font-size: 8.5pt; }}
  ul, ol {{ padding-left: 20px; }}
</style>
"""


def md_to_html(md: str) -> str:
    """Convert markdown to basic HTML. Supports headers, bold, code, tables, lists."""
    html = md
    # Headers
    html = re.sub(r"^### (.+)$", r"<h3>\1</h3>", html, flags=re.MULTILINE)
    html = re.sub(r"^## (.+)$", r"<h2>\1</h2>", html, flags=re.MULTILINE)
    html = re.sub(r"^# (.+)$", r"", html, flags=re.MULTILINE)  # h1 is in header
    # Bold
    html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
    # Code
    html = re.sub(r"`(.+?)`", r"<code>\1</code>", html)
    # Tables
    lines = html.split("\n")
    in_table = False
    result = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(set(c) <= {"-", ":", " "} for c in cells):
                continue  # separator row
            if not in_table:
                result.append("<table>")
                result.append("<tr>" + "".join(f"<th>{c}</th>" for c in cells) + "</tr>")
                in_table = True
            else:
                result.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
        else:
            if in_table:
                result.append("</table>")
                in_table = False
            if stripped.startswith("<"):
                # Pass through raw HTML/SVG blocks unchanged
                result.append(line)
            elif stripped.startswith("- "):
                result.append(f"<p>• {stripped[2:]}</p>")
            elif stripped and re.match(r"^\d+\. ", stripped):
                result.append(f"<p>{stripped}</p>")
            elif stripped:
                result.append(f"<p>{stripped}</p>")
            else:
                result.append("")
    if in_table:
        result.append("</table>")
    return "\n".join(result)


def render_pdf(
    title: str,
    subtitle: str,
    content_markdown: str,
    output_path: str,
    brand: BrandConfig | None = None,
) -> str:
    """Render markdown to a branded PDF file. Returns status message."""
    from weasyprint import HTML

    brand = brand or BrandConfig()
    body_html = md_to_html(content_markdown)
    date_str = time.strftime("%d %B %Y")
    style = _make_style(brand)

    logo_block = f'<div class="logo">{brand.logo_html}</div>' if brand.logo_html else ""
    tagline_block = f"{brand.tagline}<br>" if brand.tagline else ""
    footer_text = brand.footer or (
        f"{brand.company}"
        + (f" · {brand.address}" if brand.address else "")
        + "<br>Generated by AI"
    )

    full_html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">{style}</head><body>
<div class="header">
  <div>
    <h1>{title}</h1>
    <div class="sub">{subtitle}</div>
  </div>
  <div class="right">
    {logo_block}
    <div class="company">{brand.company}</div>
    {tagline_block}
    {date_str}
  </div>
</div>
{body_html}
<div class="footer">
{footer_text}
</div>
</body></html>"""

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=full_html).write_pdf(str(output))

    return f"PDF generated: {output_path} ({output.stat().st_size} bytes)"


# ── Agentino tool wrapper ──────────────────────────────────────────

# Default brand — set at app startup via set_default_brand()
_default_brand: BrandConfig | None = None


def set_default_brand(brand: BrandConfig) -> None:
    """Set the default brand for the generate_branded_pdf tool."""
    global _default_brand
    _default_brand = brand


@tool
async def generate_branded_pdf(
    title: str,
    subtitle: str,
    content_markdown: str,
    output_path: str,
) -> str:
    """Generate a styled, branded PDF report from markdown content.

    Creates a professional A4 PDF with company branding, accent colors,
    styled tables, and clean typography. Supports: headers, bold, tables,
    lists, code blocks, KPI cards.

    Args:
        title: Report title (e.g. "Finance Report")
        subtitle: Subtitle line (e.g. "Week of April 1-7, 2026")
        content_markdown: Report body in markdown format
        output_path: Where to save the PDF (e.g. "/tmp/finance-report.pdf")
    """
    try:
        return render_pdf(title, subtitle, content_markdown, output_path, _default_brand)
    except ImportError:
        return "Error: weasyprint not installed. Run: pip install weasyprint"
    except Exception as e:
        return f"Error generating PDF: {e}"
