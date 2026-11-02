"""verify(): deterministic verification of an ExtractedDocument.

No model here. It reads one extraction and grades it. Rule table: docs/verify_rules.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .schemas import ExtractedDocument

FieldStatus = Literal["verified", "unsupported", "missing", "not_applicable"]
Overall = Literal["verified", "needs_follow_up", "insufficient"]

REQUIRED_FIELDS = {
    "bank_statement": ("subject_name", "issuer", "period_start", "period_end", "currency", "principal_amount", "transactions"),
    "share_sale_agreement": ("subject_name", "counterparty", "document_date", "currency", "principal_amount"),
    "trust_deed": ("subject_name", "issuer", "document_date", "currency", "principal_amount"),
    "salary_letter": ("subject_name", "issuer", "document_date", "currency", "principal_amount"),
    "gift_letter": ("subject_name", "counterparty", "document_date", "currency", "principal_amount"),
}
CHECKED_FIELDS = (
    "subject_name", "issuer", "counterparty", "document_date", "period_start",
    "period_end", "currency", "principal_amount", "transactions",
)
UNIDENTIFIED_CREDIT_THRESHOLD = 50_000
GIFT_THRESHOLD = 100_000


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


def _has_evidence(extracted: ExtractedDocument, name: str) -> bool:
    # Transaction evidence is written per row, as transactions[0].amount.
    prefix = "transactions[" if name == "transactions" else None
    return any(e.field == name or (prefix and e.field.startswith(prefix)) for e in extracted.evidence)


def _field_status(extracted: ExtractedDocument, name: str, required: tuple[str, ...]) -> FieldStatus:
    if name not in required:
        return "not_applicable"
    if getattr(extracted, name) in (None, "", []):
        return "missing"
    return "verified" if _has_evidence(extracted, name) else "unsupported"


def verify(extracted: ExtractedDocument) -> VerificationResult:
    required = REQUIRED_FIELDS.get(extracted.document_type, ())
    checks = [FieldCheck(name, _field_status(extracted, name, required)) for name in CHECKED_FIELDS]

    def result(overall: Overall, reasons: list[str]) -> VerificationResult:
        return VerificationResult(extracted.document_type, overall, checks, reasons)

    # No required fields means nothing was checked, and that must not read as verified.
    if extracted.document_type not in REQUIRED_FIELDS:
        return result("insufficient", [f"Document type {extracted.document_type} has no rules, so it cannot be verified."])

    # Rule order matters: the first rule that hits decides the grade.
    failed = [c for c in checks if c.status in ("missing", "unsupported")]
    if failed:
        return result("insufficient", [f"Required field {c.field} is {c.status}." for c in failed])

    if extracted.missing_or_unclear:
        return result("needs_follow_up", [f"Flagged in the document: {note}" for note in extracted.missing_or_unclear])

    unidentified = [
        t for t in extracted.transactions
        if t.direction == "credit" and t.amount >= UNIDENTIFIED_CREDIT_THRESHOLD and not t.counterparty
    ]
    if unidentified:
        return result(
            "needs_follow_up",
            [f"Credit of {t.amount:,.2f} {t.currency} on {t.date} has no identified counterparty." for t in unidentified],
        )

    if extracted.sow_category == "gift" and (extracted.principal_amount or 0) >= GIFT_THRESHOLD:
        return result(
            "needs_follow_up",
            [f"Gift of {extracted.principal_amount:,.2f} {extracted.currency} needs the donor's source of funds."],
        )

    return result("verified", ["All required fields are evidenced and no follow-up rule was hit."])
