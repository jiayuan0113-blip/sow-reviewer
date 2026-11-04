"""Generates evals/sow_golden.evalset.json from data/cases and data/expected.

Each eval case holds the document as the user message and, as the reference, the
tool order and final disposition. Regenerate after changing a case:
    uv run python scripts/build_evalset.py
"""

from __future__ import annotations

import json
from pathlib import Path

from google.adk.evaluation.eval_case import EvalCase, IntermediateData, Invocation, SessionInput
from google.adk.evaluation.eval_set import EvalSet
from google.genai import types

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "evals" / "sow_golden.evalset.json"


def expected_tools(expected: dict) -> list[str]:
    tools = ["extraction_agent", "run_verify", "run_screening", "compute_disposition", "critic_loop"]
    if expected["expected_disposition"] == "escalate_edd":
        tools.append("escalate_to_edd")
    return tools


def build() -> EvalSet:
    index = json.loads((ROOT / "data" / "cases" / "index.json").read_text(encoding="utf-8"))["cases"]
    cases = []
    for case in index:
        expected = json.loads((ROOT / "data" / "expected" / f"{case['id']}.json").read_text(encoding="utf-8"))
        document = (ROOT / "data" / "cases" / case["file"]).read_text(encoding="utf-8")
        reference = IntermediateData(
            tool_uses=[types.FunctionCall(name=name, args={}) for name in expected_tools(expected)],
            tool_responses=[
                types.FunctionResponse(
                    name="compute_disposition",
                    response={"disposition": expected["expected_disposition"]},
                )
            ],
        )
        cases.append(
            EvalCase(
                eval_id=case["id"],
                conversation=[
                    Invocation(
                        invocation_id=f"{case['id']}-1",
                        user_content=types.Content(role="user", parts=[types.Part(text=document)]),
                        intermediate_data=reference,
                    )
                ],
                session_input=SessionInput(app_name="sow_reviewer", user_id="eval"),
            )
        )
    return EvalSet(
        eval_set_id="sow_golden",
        name="SoW golden cases",
        description="10 synthetic documents covering evidenced, follow-up, insufficient and a sanctions hit.",
        eval_cases=cases,
    )


if __name__ == "__main__":
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(build().model_dump_json(indent=2, by_alias=True, exclude_none=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
