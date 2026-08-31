"""Regex / token matchers — deterministic scorers for tasks where the
expected answer is a literal string or pattern."""

from __future__ import annotations

import re

from .base import Scorer, ScorerInput, ScorerVerdict


class TokenMatchScorer(Scorer):
    """Pass if every required token appears verbatim in the final text.

    Use for benchmark-style answers like `<YES>`/`<NO>`/`<COUNT:n>`. The
    `expected` field can be a single string or a list — every entry must
    appear (case-sensitive). harness's ECOM harness already does this
    server-side; this scorer mirrors it for projects that want the same
    contract locally without going through the harness."""

    name = "token_match"

    async def judge(self, inp: ScorerInput) -> ScorerVerdict:
        tokens = inp.expected
        if tokens is None:
            return ScorerVerdict(score=0.0, score_detail=["no expected tokens"])
        if isinstance(tokens, str):
            tokens = [tokens]
        text = inp.final_text or ""
        missing = [t for t in tokens if t not in text]
        if missing:
            return ScorerVerdict(
                score=0.0,
                score_detail=[f"missing token(s): {', '.join(missing)}"],
            )
        return ScorerVerdict(score=1.0)


class RegexMatchScorer(Scorer):
    """Pass if the final text matches a regex. Use for fuzzier formats."""

    name = "regex_match"

    async def judge(self, inp: ScorerInput) -> ScorerVerdict:
        pattern = inp.expected
        if not pattern:
            return ScorerVerdict(score=0.0, score_detail=["no expected pattern"])
        try:
            if re.search(pattern, inp.final_text or "", re.MULTILINE):
                return ScorerVerdict(score=1.0)
            return ScorerVerdict(
                score=0.0,
                score_detail=[f"pattern {pattern!r} not found"],
            )
        except re.error as exc:
            return ScorerVerdict(score=0.0, score_detail=[f"bad regex: {exc}"])
