"""Runs every case through root_agent and records latency, tokens and estimated cost.

    uv run python scripts/metrics.py            # writes outputs/metrics.json
    uv run python scripts/metrics.py sow-003    # one case

Needs a model credential. A case that reaches the human confirmation stops there:
latency excludes human wait, and the escalation is not approved.

Tokens are counted with a plugin, not from runner events: AgentTool runs its agent
in a nested runner whose events never reach the caller, but plugins are inherited.

SOW_TRACE_TO_CLOUD=1 sends spans to Cloud Trace (needs `uv sync --extra gcp` and ADC).
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.genai import types

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "sow_reviewer" / ".env")

APP_NAME = "sow_reviewer"
USER_ID = "metrics"
CONFIRMATION_CALL = "adk_request_confirmation"


class UsagePlugin(BasePlugin):
    """Sums token usage per model across every model call, including nested ones."""

    def __init__(self) -> None:
        super().__init__(name="usage")
        self.by_model: dict[str, dict[str, int]] = {}
        self._model_by_agent: dict[str, str] = {}

    def reset(self) -> None:
        self.by_model = {}

    async def before_model_callback(self, *, callback_context, llm_request):
        self._model_by_agent[callback_context.agent_name] = llm_request.model or "unknown"
        return None

    async def after_model_callback(self, *, callback_context, llm_response):
        usage = llm_response.usage_metadata
        if usage is None or llm_response.partial:
            return None
        model = self._model_by_agent.get(callback_context.agent_name, "unknown")
        row = self.by_model.setdefault(model, {"calls": 0, "input": 0, "output": 0, "thoughts": 0})
        row["calls"] += 1
        row["input"] += usage.prompt_token_count or 0
        row["output"] += usage.candidates_token_count or 0
        row["thoughts"] += usage.thoughts_token_count or 0
        return None


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile. With 10 cases, p95 is the slowest case."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


def estimate_cost(by_model: dict[str, dict[str, int]], pricing: dict, today: str) -> float | None:
    """USD from config/pricing.json. None if any model used has no price on file."""
    total = 0.0
    for model, usage in by_model.items():
        price = pricing.get("models", {}).get(model)
        if not price or price.get("input") is None or price.get("output") is None:
            return None
        if price.get("valid_through") and today > price["valid_through"]:
            price = price["after"]
        total += usage["input"] / 1e6 * price["input"]
        total += (usage["output"] + usage["thoughts"]) / 1e6 * price["output"]
    return total


def maybe_enable_cloud_trace() -> bool:
    """Same setup `adk web --otel_to_cloud` uses, limited to traces."""
    if os.environ.get("SOW_TRACE_TO_CLOUD") != "1":
        return False
    import google.auth
    from google.adk.telemetry.google_cloud import _CLOUD_PLATFORM_SCOPE, get_gcp_exporters, get_gcp_resource
    from google.adk.telemetry.setup import maybe_set_otel_providers

    credentials, project_id = google.auth.default(scopes=[_CLOUD_PLATFORM_SCOPE])
    maybe_set_otel_providers(
        otel_hooks_to_setup=[
            get_gcp_exporters(enable_cloud_tracing=True, google_auth=(credentials, project_id))
        ],
        otel_resource=get_gcp_resource(project_id),
    )
    return True


async def run_case(runner: Runner, usage: UsagePlugin, case: dict, pricing: dict) -> dict:
    document = (ROOT / "data" / "cases" / case["file"]).read_text(encoding="utf-8")
    expected = json.loads((ROOT / "data" / "expected" / f"{case['id']}.json").read_text(encoding="utf-8"))
    session = await runner.session_service.create_session(app_name=APP_NAME, user_id=USER_ID)
    usage.reset()
    paused = False
    error = None
    started = time.perf_counter()
    try:
        async for event in runner.run_async(
            user_id=USER_ID,
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text=document)]),
        ):
            paused = paused or any(c.name == CONFIRMATION_CALL for c in event.get_function_calls())
    except Exception as exc:  # A failed case is a data point, not a reason to lose the run.
        error = f"{type(exc).__name__}: {exc}"
    latency = time.perf_counter() - started

    final = await runner.session_service.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=session.id)
    disposition = final.state.get("disposition")
    return {
        "case": case["id"],
        "latency_s": round(latency, 2),
        "tokens": usage.by_model,
        "total_tokens": sum(u["input"] + u["output"] + u["thoughts"] for u in usage.by_model.values()),
        "est_cost_usd": estimate_cost(usage.by_model, pricing, time.strftime("%Y-%m-%d")),
        "disposition": disposition,
        "expected_disposition": expected["expected_disposition"],
        "disposition_ok": disposition == expected["expected_disposition"],
        "paused_for_confirmation": paused,
        "critic_approved": final.state.get("critic_approved"),
        "error": error,
    }


def summarize(rows: list[dict]) -> dict:
    completed = [r for r in rows if not r["error"]]
    latencies = [r["latency_s"] for r in completed]
    costs = [r["est_cost_usd"] for r in completed if r["est_cost_usd"] is not None]
    return {
        "cases": len(rows),
        "errors": len(rows) - len(completed),
        "latency_p50_s": percentile(latencies, 50),
        "latency_p95_s": percentile(latencies, 95),
        "total_tokens": sum(r["total_tokens"] for r in completed),
        "mean_cost_per_case_usd": statistics.mean(costs) if len(costs) == len(completed) and costs else None,
        "disposition_correct": sum(r["disposition_ok"] for r in rows),
    }


async def main(only: str | None) -> None:
    from sow_reviewer.agent import root_agent, sanctions_toolset

    traced = maybe_enable_cloud_trace()
    pricing = json.loads((ROOT / "config" / "pricing.json").read_text(encoding="utf-8"))
    index = json.loads((ROOT / "data" / "cases" / "index.json").read_text(encoding="utf-8"))["cases"]
    usage = UsagePlugin()
    runner = Runner(
        agent=root_agent, app_name=APP_NAME, session_service=InMemorySessionService(), plugins=[usage]
    )
    rows = []
    try:
        for case in index:
            if only and case["id"] != only:
                continue
            row = await run_case(runner, usage, case, pricing)
            rows.append(row)
            print(
                f"{row['case']}: {row['latency_s']:7.2f}s {row['total_tokens']:7d} tok "
                f"cost={row['est_cost_usd']} disposition={row['disposition']} "
                f"(expected {row['expected_disposition']}){' PAUSED' if row['paused_for_confirmation'] else ''}"
                f"{' ERROR ' + row['error'] if row['error'] else ''}"
            )
    finally:
        await runner.close()
        # run_screening calls the MCP server from code, so the toolset is not in the agent tree.
        await sanctions_toolset.close()
    summary = summarize(rows)
    print(json.dumps(summary, indent=2))
    out = ROOT / "outputs" / "metrics.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(
        json.dumps({"summary": summary, "cases": rows, "cloud_trace": traced, "pricing_read_on": pricing["read_on"]}, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))
