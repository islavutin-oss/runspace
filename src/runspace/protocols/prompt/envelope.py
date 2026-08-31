"""Per-turn user-message envelope."""

from __future__ import annotations


def build_message_envelope(
    message: str,
    *,
    company: str | None = None,
    user_name: str | None = None,
    user_role: str | None = None,
    memory_block: str | None = None,
) -> str:
    """Prepend `[company:..., user:..., role:...]` and (optionally) durable
    memories to the user message.

    Order (matches AppRegistry):
      1. memory_block (if any) — followed by blank line
      2. `[meta]` line — followed by newline
      3. message
    """
    meta_parts: list[str] = []
    if company:
        meta_parts.append(f"company: {company}")
    if user_name:
        meta_parts.append(f"user: {user_name}")
    if user_role:
        meta_parts.append(f"role: {user_role}")

    out = message
    if meta_parts:
        out = "[" + ", ".join(meta_parts) + "]\n" + out
    if memory_block:
        out = f"{memory_block}\n\n{out}"
    return out
