import json
from pathlib import Path

from sow_reviewer.schemas import ExtractedDocument

ROOT = Path(__file__).resolve().parent.parent


def test_schema_roundtrip():
    doc = ExtractedDocument(
        document_type="gift_letter",
        subject_name="Wong Jun Hao",
        counterparty="Wong Siew Lan",
        document_date="2026-08-02",
        currency="SGD",
        principal_amount=350000.0,
        sow_category="gift",
        evidence=[{"field": "principal_amount", "quote": "Singapore Dollars Three Hundred and Fifty Thousand (S$350,000.00)"}],
    )
    assert ExtractedDocument.model_validate_json(doc.model_dump_json()) == doc


def salary_with_components(**overrides):
    values = dict(
        document_type="salary_letter",
        subject_name="Rajesh Pillai",
        currency="SGD",
        sow_category="employment_income",
        amount_components=[
            {"label": "Base salary", "amount": 17500.0, "frequency": "monthly", "quote": "Base salary 17,500.00 Monthly"},
            {"label": "Fixed allowance", "amount": 800.0, "frequency": "monthly", "quote": "Fixed allowance 800.00 Monthly"},
            {"label": "Annual wage supplement", "amount": 17500.0, "frequency": "annual", "quote": "Annual wage supplement 17,500.00"},
            {"label": "Performance bonus", "amount": 63000.0, "frequency": "annual", "quote": "Performance bonus 63,000.00"},
        ],
        evidence=[],
    )
    return ExtractedDocument(**{**values, **overrides})


def test_components_are_summed_by_code_and_quoted_as_evidence():
    doc = salary_with_components()
    assert doc.principal_amount == 300100.0
    assert len([e for e in doc.evidence if e.field == "principal_amount"]) == 4


def test_code_total_overrides_a_total_from_the_model():
    assert salary_with_components(principal_amount=1.0).principal_amount == 300100.0


def test_revalidating_does_not_duplicate_evidence():
    doc = salary_with_components()
    assert ExtractedDocument.model_validate(doc.model_dump()) == doc


def test_components_are_ignored_outside_salary_letters():
    doc = salary_with_components(document_type="gift_letter", principal_amount=5.0)
    assert doc.principal_amount == 5.0 and doc.evidence == []


def test_case_index_files_exist():
    index = json.loads((ROOT / "data/cases/index.json").read_text())
    for case in index["cases"]:
        assert (ROOT / "data/cases" / case["file"]).exists(), case["file"]
        assert (ROOT / "data/expected" / f"{case['id']}.json").exists(), case["id"]
