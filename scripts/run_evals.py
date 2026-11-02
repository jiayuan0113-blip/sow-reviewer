"""Scores extractions against data/expected without calling a model.

Reads outputs/extracted/<case>.json (written by scripts/extract_all.py), runs
verify() on each, and scores: verify grade, planted issues flagged, field accuracy.
It also screens each subject through sanctions_lookup. `adk eval` covers the
end-to-end trajectory; this script covers the deterministic grading.

    uv run python scripts/extract_all.py
    uv run python scripts/run_evals.py     # writes outputs/eval_report.json
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sow_reviewer.schemas import ExtractedDocument  # noqa: E402

SCORED_FIELDS = (
    "document_type", "subject_name", "issuer", "counterparty", "document_date",
    "period_start", "period_end", "currency", "principal_amount", "sow_category",
)


def same(expected, got) -> bool:
    """Documents print names in capitals and labels use title case, so case is not an error."""
    if isinstance(expected, str) and isinstance(got, str):
        return expected.casefold() == got.casefold()
    return expected == got


def score_fields(expected: dict, extracted: dict) -> dict:
    wrong = {}
    total = 0
    for field in SCORED_FIELDS:
        if field not in expected:
            continue
        total += 1
        value = extracted.get(field)
        if not same(expected[field], value):
            wrong[field] = {"expected": expected[field], "got": value}
    return {"correct": total - len(wrong), "total": total, "wrong": wrong}


def score_flags(expected: dict, flags: list[str]) -> dict:
    """Each planted issue passes if any of its keywords appears in the model's flags."""
    text = " ".join(flags).casefold()
    groups = expected.get("must_flag_keywords", [])
    missed = [group for group in groups if not any(word.casefold() in text for word in group)]
    return {"found": len(groups) - len(missed), "total": len(groups), "missed": missed}


def score_case(case_id: str, expected: dict, extracted: dict, verify, sanctions_lookup) -> dict:
    doc = ExtractedDocument.model_validate(extracted)
    result = verify(doc)
    hit = bool(sanctions_lookup(expected["subject_name"])["matches"])
    return {
        "case": case_id,
        "expected_overall": expected["expected_overall"],
        "overall": result.overall,
        "overall_ok": result.overall == expected["expected_overall"],
        "sanctions_ok": hit == expected["sanctions_hit"],
        "flags": score_flags(expected, doc.missing_or_unclear),
        "fields": score_fields(expected, extracted),
        "reasons": dataclasses.asdict(result)["reasons"],
    }


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    return {
        "cases": n,
        "overall_correct": sum(r["overall_ok"] for r in rows),
        "sanctions_correct": sum(r["sanctions_ok"] for r in rows),
        "planted_found": sum(r["flags"]["found"] for r in rows),
        "planted_total": sum(r["flags"]["total"] for r in rows),
        "fields_correct": sum(r["fields"]["correct"] for r in rows),
        "fields_total": sum(r["fields"]["total"] for r in rows),
    }


def main() -> int:
    from sow_reviewer.verify import verify
    from tools.sanctions_server import sanctions_lookup

    index = json.loads((ROOT / "data" / "cases" / "index.json").read_text(encoding="utf-8"))["cases"]
    rows, absent = [], []
    for case in index:
        path = ROOT / "outputs" / "extracted" / f"{case['id']}.json"
        if not path.exists():
            absent.append(case["id"])
            continue
        expected = json.loads((ROOT / "data" / "expected" / f"{case['id']}.json").read_text(encoding="utf-8"))
        row = score_case(case["id"], expected, json.loads(path.read_text(encoding="utf-8")), verify, sanctions_lookup)
        rows.append(row)
        print(
            f"{row['case']}: verify {row['overall']:16s} {'ok ' if row['overall_ok'] else 'BAD'} "
            f"| sanctions {'ok ' if row['sanctions_ok'] else 'BAD'} "
            f"| planted {row['flags']['found']}/{row['flags']['total']} "
            f"| fields {row['fields']['correct']}/{row['fields']['total']}"
        )
    if absent:
        print(f"Not scored, no extraction on disk: {', '.join(absent)}. Run scripts/extract_all.py first.")
    if not rows:
        return 1
    summary = summarize(rows)
    print(json.dumps(summary, indent=2))
    out = ROOT / "outputs" / "eval_report.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "cases": rows}, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
