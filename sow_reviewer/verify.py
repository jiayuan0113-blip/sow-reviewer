"""verify(): deterministic verification of an ExtractedDocument.

Hand-written by Joyce on 2026-10-29. Rule table: docs/verify_rules.md.
Do NOT generate this file with an AI assistant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .schemas import ExtractedDocument

FieldStatus = Literal["verified", "unsupported", "missing", "not_applicable"]
Overall = Literal["verified", "needs_follow_up", "insufficient"]


@dataclass
class FieldCheck:
    field: str
    status: FieldStatus


@dataclass
class VerificationResult:
    document_type: str
    overall: Overall
    fields: list[FieldCheck] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def verify(extracted: ExtractedDocument) -> VerificationResult:
    raise NotImplementedError("hand-write me on 2026-10-29, see docs/verify_rules.md")
