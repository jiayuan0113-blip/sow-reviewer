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


def test_case_index_files_exist():
    index = json.loads((ROOT / "data/cases/index.json").read_text())
    for case in index["cases"]:
        assert (ROOT / "data/cases" / case["file"]).exists(), case["file"]
        assert (ROOT / "data/expected" / f"{case['id']}.json").exists(), case["id"]
