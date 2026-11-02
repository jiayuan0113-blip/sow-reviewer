"""The eval files parse with the classes `adk eval` uses, and the scorers behave."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from google.adk.evaluation.eval_case import IntermediateData, Invocation
from google.adk.evaluation.eval_config import get_eval_metrics_from_config, get_evaluation_criteria_or_default
from google.adk.evaluation.evaluator import EvalStatus
from google.adk.evaluation.local_eval_sets_manager import load_eval_set_from_file
from google.genai import types

from scripts.build_evalset import OUT, build
from scripts.metrics import estimate_cost, percentile
from scripts.run_evals import score_fields, score_flags, summarize
from sow_reviewer.eval_metrics import disposition_match

ROOT = Path(__file__).resolve().parent.parent
INDEX = json.loads((ROOT / "data/cases/index.json").read_text(encoding="utf-8"))["cases"]


def expected(case_id):
    return json.loads((ROOT / f"data/expected/{case_id}.json").read_text(encoding="utf-8"))


def test_ten_cases_with_all_outcomes_and_one_sanctions_hit():
    assert len(INDEX) == 10
    all_expected = [expected(c["id"]) for c in INDEX]
    assert {e["expected_overall"] for e in all_expected} == {"verified", "needs_follow_up", "insufficient"}
    assert sum(e["sanctions_hit"] for e in all_expected) == 1
    assert all(c["planted_issue"] for c in INDEX)


def test_new_cases_are_marked_synthetic():
    for case in INDEX:
        assert "SYNTHETIC DOCUMENT" in (ROOT / "data/cases" / case["file"]).read_text(encoding="utf-8")


def test_evalset_loads_the_way_adk_eval_loads_it():
    eval_set = load_eval_set_from_file(str(OUT), "sow_golden")
    assert [c.eval_id for c in eval_set.eval_cases] == [c["id"] for c in INDEX]


def test_evalset_file_is_in_sync_with_the_cases():
    on_disk = json.loads(OUT.read_text(encoding="utf-8"))
    assert on_disk == json.loads(build().model_dump_json(by_alias=True, exclude_none=True))


def test_test_config_parses_and_sits_beside_the_evalset():
    # adk eval auto-loads <evalset dir>/test_config.json.
    config = get_evaluation_criteria_or_default(str(OUT.parent / "test_config.json"))
    names = {m.metric_name for m in get_eval_metrics_from_config(config)}
    assert {"tool_trajectory_avg_score", "disposition_match"} <= names


def test_custom_metric_resolves_under_adk_evals_module_name(tmp_path):
    # adk eval imports the agent package as "agent", with no repo root on sys.path.
    code = (
        "import asyncio\n"
        "from google.adk.cli.cli_eval import _get_agent_module\n"
        f"_get_agent_module({str(ROOT / 'sow_reviewer')!r})\n"
        "from google.adk.evaluation.custom_metric_evaluator import _get_metric_function\n"
        "print(_get_metric_function('agent.eval_metrics.disposition_match').__name__)\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GOOGLE_", "RAG_"))}
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, env=env, check=True, capture_output=True, text=True, timeout=90
    ).stdout
    assert "disposition_match" in out


def _invocation(disposition):
    return Invocation(
        user_content=types.Content(role="user", parts=[types.Part(text="doc")]),
        intermediate_data=IntermediateData(
            tool_responses=[types.FunctionResponse(name="compute_disposition", response={"disposition": disposition})]
        ),
    )


def test_disposition_metric_passes_and_fails():
    good = disposition_match(None, [_invocation("follow_up")], [_invocation("follow_up")])
    assert good.overall_score == 1.0 and good.overall_eval_status == EvalStatus.PASSED
    bad = disposition_match(None, [_invocation("evidenced")], [_invocation("follow_up")])
    assert bad.overall_score == 0.0 and bad.overall_eval_status == EvalStatus.FAILED


def test_score_flags_uses_keyword_groups():
    case = {"must_flag_keywords": [["cash"], ["usd", "dollar"]]}
    assert score_flags(case, ["Cash deposit has no source."]) == {"found": 1, "total": 2, "missed": [["usd", "dollar"]]}


def test_score_fields_ignores_name_case_but_not_values():
    case = {"subject_name": "Lim Hui Ling", "currency": "SGD", "principal_amount": 300100.0}
    got = {"subject_name": "LIM HUI LING", "currency": "USD", "principal_amount": 300100.0}
    result = score_fields(case, got)
    assert result["correct"] == 2 and result["total"] == 3
    assert list(result["wrong"]) == ["currency"]


def test_summarize_totals():
    row = {
        "overall_ok": True, "sanctions_ok": False,
        "flags": {"found": 1, "total": 2}, "fields": {"correct": 3, "total": 4},
    }
    assert summarize([row, row])["planted_total"] == 4
    assert summarize([row, row])["overall_correct"] == 2


def test_percentile_nearest_rank():
    values = [float(v) for v in range(1, 11)]
    assert percentile(values, 50) == 5.0
    assert percentile(values, 95) == 10.0
    assert percentile([], 50) is None


def test_cost_estimate_uses_price_period_and_refuses_unknown_models():
    pricing = {"models": {"m": {"input": 1.0, "output": 2.0, "valid_through": "2026-12-31", "after": {"input": 10.0, "output": 20.0}}}}
    usage = {"m": {"calls": 1, "input": 1_000_000, "output": 500_000, "thoughts": 500_000}}
    assert estimate_cost(usage, pricing, "2026-11-02") == 3.0
    assert estimate_cost(usage, pricing, "2027-01-01") == 30.0
    assert estimate_cost({"other": usage["m"]}, pricing, "2026-11-02") is None


def test_pricing_config_covers_the_model_in_use():
    from sow_reviewer.agent import MODEL

    pricing = json.loads((ROOT / "config/pricing.json").read_text(encoding="utf-8"))
    assert MODEL in pricing["models"]


def test_setup_rag_uploads_the_policy_but_not_the_source_list():
    from scripts.setup_rag import policy_files

    assert [p.name for p in policy_files()] == ["internal_sow_policy.md"]


def test_policy_answers_the_gift_question_and_matches_the_rule_table():
    policy = (ROOT / "data/policies/internal_sow_policy.md").read_text(encoding="utf-8")
    assert "FICTIONAL" in policy
    assert "4.2 A gift of 100,000 or more requires follow-up" in policy
    assert "3.1 A single credit of 50,000 or more with no identifiable counterparty" in policy
    rules = (ROOT / "docs/verify_rules.md").read_text(encoding="utf-8")
    assert "≥ 100,000" in rules and "≥ 50,000" in rules


def test_sanctions_list_has_no_case_subject_except_the_planted_one():
    entries = json.loads((ROOT / "data/sanctions_synthetic.json").read_text(encoding="utf-8"))
    names = {n.casefold() for e in entries for n in [e["name"], *e["aliases"]]}
    for case in INDEX:
        assert (case["subject"].casefold() in names) == (case["id"] == "sow-010"), case["id"]
