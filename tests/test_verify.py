"""Behaviour of verify() per docs/verify_rules.md.

These fail with NotImplementedError until sow_reviewer/verify.py is written.
Documents are built by hand. Evidence for transactions uses the field-spec form
`transactions[i].amount`, so the `transactions` field counts as quoted when any
`transactions[...]` evidence entry exists.
"""

from __future__ import annotations

import pytest

from sow_reviewer.schemas import ExtractedDocument
from sow_reviewer.verify import verify

QUOTE = "a verbatim excerpt from the document"


def evidence(*fields: str) -> list[dict]:
    return [{"field": f, "quote": QUOTE} for f in fields]


def status(result, field: str) -> str:
    matches = [f.status for f in result.fields if f.field == field]
    assert matches, f"verify() returned no FieldCheck for {field!r}"
    return matches[0]


def bank_statement(**overrides) -> ExtractedDocument:
    """Passes every rule: a salary credit with a counterparty and no flags."""
    values = dict(
        document_type="bank_statement",
        subject_name="Lim Hui Ling",
        issuer="Straits Mercantile Bank Limited",
        period_start="2026-07-01",
        period_end="2026-09-30",
        currency="SGD",
        principal_amount=406970.55,
        sow_category="employment_income",
        transactions=[credit(18500.0, "Nexora Analytics Pte Ltd")],
        evidence=evidence(
            "subject_name", "issuer", "period_start", "period_end", "currency",
            "principal_amount", "transactions[0].amount",
        ),
    )
    return ExtractedDocument(**{**values, **overrides})


def credit(amount: float, counterparty: str | None, direction: str = "credit") -> dict:
    return dict(
        date="2026-07-15", description="LINE", direction=direction,
        amount=amount, currency="SGD", counterparty=counterparty,
    )


def gift_letter(amount: float, **overrides) -> ExtractedDocument:
    values = dict(
        document_type="gift_letter",
        subject_name="Wong Jun Hao",
        issuer="Wong Siew Lan",
        counterparty="Wong Siew Lan",
        document_date="2026-08-02",
        currency="SGD",
        principal_amount=amount,
        sow_category="gift",
        evidence=evidence("subject_name", "counterparty", "document_date", "currency", "principal_amount"),
    )
    return ExtractedDocument(**{**values, **overrides})


def salary_letter(**overrides) -> ExtractedDocument:
    values = dict(
        document_type="salary_letter",
        subject_name="Rajesh Pillai",
        issuer="Orion Semiconductor Asia Pte Ltd",
        counterparty="Orion Semiconductor Asia Pte Ltd",
        document_date="2026-07-06",
        period_start="2019-02-01",
        currency="SGD",
        principal_amount=300100.0,
        sow_category="employment_income",
        evidence=evidence("subject_name", "issuer", "document_date", "currency", "principal_amount"),
    )
    return ExtractedDocument(**{**values, **overrides})


# --- the five synthetic cases, values from data/expected/ ---


def test_sow_001_cash_deposit_needs_follow_up():
    doc = bank_statement(
        document_date="2026-10-02",
        sow_category="unknown",
        transactions=[
            credit(60000.0, None),
            credit(420000.0, "Harbourview Ventures Pte Ltd"),
            credit(300000.0, None, direction="debit"),
        ],
    )
    result = verify(doc)
    assert result.overall == "needs_follow_up"
    assert result.document_type == "bank_statement"
    assert status(result, "transactions") == "verified"
    assert result.reasons


def test_sow_002_unclear_second_tranche_needs_follow_up():
    doc = ExtractedDocument(
        document_type="share_sale_agreement",
        subject_name="Tan Wei Ming",
        counterparty="Harbourview Ventures Pte Ltd",
        document_date="2026-04-28",
        currency="SGD",
        principal_amount=2750000.0,
        sow_category="business_sale",
        evidence=evidence("subject_name", "counterparty", "document_date", "currency", "principal_amount"),
        missing_or_unclear=["Tranche 2 (2,330,000) has no firm payment date; it depends on an earn-out."],
    )
    result = verify(doc)
    assert result.overall == "needs_follow_up"
    assert result.reasons


def test_sow_003_two_currencies_needs_follow_up():
    doc = ExtractedDocument(
        document_type="trust_deed",
        subject_name="Chua Mei Xin",
        issuer="Meridian Fiduciary Services (Jersey) Limited",
        counterparty="Meridian Fiduciary Services (Jersey) Limited",
        document_date="2026-03-10",
        currency="SGD",
        principal_amount=1200000.0,
        sow_category="inheritance_or_trust",
        evidence=evidence("subject_name", "issuer", "document_date", "currency", "principal_amount"),
        missing_or_unclear=["Initial trust fund is stated in USD (5,000,000); the distribution is in SGD."],
    )
    assert verify(doc).overall == "needs_follow_up"


def test_sow_004_salary_letter_verified():
    result = verify(salary_letter())
    assert result.overall == "verified"
    for field in ("subject_name", "issuer", "document_date", "currency", "principal_amount"):
        assert status(result, field) == "verified"


def test_sow_005_large_gift_needs_follow_up():
    doc = gift_letter(
        350000.0,
        missing_or_unclear=["Donor's source of funds (apartment sale, June 2026) has no supporting document."],
    )
    assert verify(doc).overall == "needs_follow_up"


# --- field statuses ---


def test_field_without_evidence_is_unsupported_and_document_insufficient():
    doc = salary_letter(evidence=evidence("subject_name", "issuer", "document_date", "currency"))
    result = verify(doc)
    assert status(result, "principal_amount") == "unsupported"
    assert result.overall == "insufficient"
    assert result.reasons


def test_required_field_empty_is_missing_and_document_insufficient():
    doc = salary_letter(document_date=None, evidence=evidence("subject_name", "issuer", "currency", "principal_amount"))
    result = verify(doc)
    assert status(result, "document_date") == "missing"
    assert result.overall == "insufficient"


def test_bank_statement_without_transactions_is_insufficient():
    doc = bank_statement(
        transactions=[],
        evidence=evidence("subject_name", "issuer", "period_start", "period_end", "currency", "principal_amount"),
    )
    result = verify(doc)
    assert status(result, "transactions") == "missing"
    assert result.overall == "insufficient"


def test_transactions_without_evidence_are_unsupported():
    doc = bank_statement(
        evidence=evidence("subject_name", "issuer", "period_start", "period_end", "currency", "principal_amount"),
    )
    result = verify(doc)
    assert status(result, "transactions") == "unsupported"
    assert result.overall == "insufficient"


def test_field_not_required_for_this_type_is_not_applicable():
    # Salary letters do not require period_start, even when the document has one.
    assert status(verify(salary_letter()), "period_start") == "not_applicable"
    # Bank statements do not require counterparty.
    assert status(verify(bank_statement()), "counterparty") == "not_applicable"


# --- rule order: first hit wins ---


def test_insufficient_beats_follow_up():
    doc = salary_letter(document_date=None, missing_or_unclear=["Letter has no date."])
    assert verify(doc).overall == "insufficient"


def test_unclear_items_alone_trigger_follow_up():
    assert verify(salary_letter(missing_or_unclear=["Bonus year is unclear."])).overall == "needs_follow_up"


# --- rule 3 boundary: single credit >= 50,000 with no counterparty ---


def test_rule3_credit_just_below_threshold_passes():
    assert verify(bank_statement(transactions=[credit(49999.0, None)])).overall == "verified"


def test_rule3_credit_at_threshold_needs_follow_up():
    result = verify(bank_statement(transactions=[credit(50000.0, None)]))
    assert result.overall == "needs_follow_up"
    assert result.reasons


def test_rule3_counterparty_present_passes():
    assert verify(bank_statement(transactions=[credit(50000.0, "Harbourview Ventures Pte Ltd")])).overall == "verified"


def test_rule3_ignores_debits():
    assert verify(bank_statement(transactions=[credit(50000.0, None, direction="debit")])).overall == "verified"


def test_rule3_looks_at_every_transaction():
    txns = [credit(18500.0, "Nexora Analytics Pte Ltd"), credit(60000.0, None)]
    assert verify(bank_statement(transactions=txns)).overall == "needs_follow_up"


# --- rule 4 boundary: gift >= 100,000 ---


def test_rule4_gift_just_below_threshold_passes():
    assert verify(gift_letter(99999.0)).overall == "verified"


def test_rule4_gift_at_threshold_needs_follow_up():
    result = verify(gift_letter(100000.0))
    assert result.overall == "needs_follow_up"
    assert result.reasons


def test_rule4_applies_only_to_gift_category():
    assert verify(salary_letter(principal_amount=100000.0)).overall == "verified"


@pytest.mark.parametrize("field", ["subject_name", "counterparty", "document_date", "currency", "principal_amount"])
def test_gift_letter_required_fields_need_evidence(field):
    fields = ["subject_name", "counterparty", "document_date", "currency", "principal_amount"]
    fields.remove(field)
    doc = gift_letter(50000.0, evidence=evidence(*fields))
    result = verify(doc)
    assert status(result, field) == "unsupported"
    assert result.overall == "insufficient"


# --- no fail-open paths ---


def test_other_document_type_is_never_verified():
    doc = ExtractedDocument(document_type="other", subject_name="A", sow_category="unknown", evidence=[])
    result = verify(doc)
    assert result.overall == "insufficient"
    assert result.reasons


def test_gift_category_without_amount_does_not_crash():
    doc = ExtractedDocument(document_type="other", subject_name="A", sow_category="gift", evidence=[])
    assert verify(doc).overall == "insufficient"


def test_rule3_blank_counterparty_counts_as_none():
    assert verify(bank_statement(transactions=[credit(50000.0, "")])).overall == "needs_follow_up"

