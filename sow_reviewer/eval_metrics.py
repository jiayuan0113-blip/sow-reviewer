"""Custom `adk eval` metric: did the run reach the expected disposition?

Reads the compute_disposition tool response, so no judge model and no cost. It
also works on the EDD case, which pauses for a human and has no final text.

test_config.json names this as agent.eval_metrics.disposition_match because
`adk eval` imports sow_reviewer/__init__.py under the module name "agent".
"""

from __future__ import annotations

from google.adk.evaluation.eval_case import Invocation, get_all_tool_responses
from google.adk.evaluation.eval_metrics import EvalMetric
from google.adk.evaluation.evaluator import EvalStatus, EvaluationResult, PerInvocationResult


def _disposition(invocation: Invocation | None) -> str | None:
    if invocation is None:
        return None
    for response in get_all_tool_responses(invocation.intermediate_data):
        if response.name == "compute_disposition" and response.response:
            return response.response.get("disposition")
    return None


def disposition_match(
    eval_metric: EvalMetric,
    actual_invocations: list[Invocation],
    expected_invocations: list[Invocation] | None = None,
    conversation_scenario=None,
) -> EvaluationResult:
    expected_invocations = expected_invocations or []
    per_invocation = []
    for actual, expected in zip(actual_invocations, expected_invocations):
        want = _disposition(expected)
        score = 1.0 if want is not None and _disposition(actual) == want else 0.0
        per_invocation.append(
            PerInvocationResult(
                actual_invocation=actual,
                expected_invocation=expected,
                score=score,
                eval_status=EvalStatus.PASSED if score == 1.0 else EvalStatus.FAILED,
            )
        )
    if not per_invocation:
        return EvaluationResult()
    overall = sum(r.score for r in per_invocation) / len(per_invocation)
    return EvaluationResult(
        overall_score=overall,
        overall_eval_status=EvalStatus.PASSED if overall == 1.0 else EvalStatus.FAILED,
        per_invocation_results=per_invocation,
    )
