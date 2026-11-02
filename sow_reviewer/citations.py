"""Deterministic citation and amount checks used by the critic.

No model here. The critic calls these through a tool, so "does this quote exist"
and "does this number exist" are answered by string comparison, not by opinion.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_AMOUNT = re.compile(r"(?<![\d.,])(?:\d{1,3}(?:,\d{3})+|\d{4,})(?:\.\d+)?")
_CLAUSE = re.compile(r"SOW-POL\s+\d+(?:\.\d+)?")


def normalize(text: str) -> str:
    """Drops markdown emphasis and table pipes, collapses whitespace, ignores case."""
    # Emphasis marks go without a space, so "**198,000.00**, paid" still reads "198,000.00, paid".
    without_markup = re.sub(r"[*_]", "", text).replace("|", " ")
    return " ".join(without_markup.split()).casefold()


def amounts_in(text: str) -> set[float]:
    """Numbers of 1,000 or more. Smaller ones are dates, clause numbers and counts."""
    return {float(m.replace(",", "")) for m in _AMOUNT.findall(text)}


def clauses_in(text: str) -> set[str]:
    return {" ".join(m.split()) for m in _CLAUSE.findall(text)}


def unsupported_quotes(quotes: Iterable[str], document: str) -> list[str]:
    """Quotes that do not appear in the document."""
    haystack = normalize(document)
    return [q for q in quotes if normalize(q) not in haystack]


def unsupported_amounts(report: str, sources: Iterable[str], known: Iterable[float] = ()) -> list[float]:
    """Amounts in the report found in no source text and in no known extracted value."""
    allowed = set(known)
    for source in sources:
        allowed |= amounts_in(source)
    return sorted(a for a in amounts_in(report) if a not in allowed)


def unsupported_clauses(report: str, policy_answer: str) -> list[str]:
    """Policy clauses cited in the report that the policy step did not return."""
    return sorted(clauses_in(report) - clauses_in(policy_answer))
