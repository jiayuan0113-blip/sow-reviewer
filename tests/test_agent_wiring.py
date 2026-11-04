"""Runs the real agent tree against a scripted fake model, with no credentials.

verify() and the MCP server are stubbed with canned results where a test needs a
result: this file checks wiring (order, state, loop cap, confirmation), not rules.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.adk.tools.tool_confirmation import ToolConfirmation
from google.genai import types

from sow_reviewer import agent as agent_module
from sow_reviewer import review_tools
from sow_reviewer.schemas import ExtractedDocument
from sow_reviewer.verify import FieldCheck, VerificationResult

REAL_LOOKUP = agent_module._lookup_sanctions

pytestmark = pytest.mark.anyio

ROOT = Path(__file__).resolve().parent.parent
DOCUMENT = "Annual remuneration is SGD 198,000.00 for Mr Orrin Talvek Drossmere."
CONFIRM = "adk_request_confirmation"


@pytest.fixture
def anyio_backend():
    return "asyncio"


class ScriptedLlm(BaseLlm):
    """Replies with the next scripted item; records what each request contained."""

    script: list = []
    requests: list = []

    async def generate_content_async(self, llm_request, stream=False):
        self.requests.append(llm_request)
        step = self.script.pop(0)
        step = step(llm_request) if callable(step) else step
        if isinstance(step, tuple):
            part = types.Part(function_call=types.FunctionCall(name=step[0], args=step[1]))
        else:
            part = types.Part(text=step)
        yield LlmResponse(
            content=types.Content(role="model", parts=[part]),
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=10, candidates_token_count=5, total_token_count=15
            ),
        )


def fake(*script) -> ScriptedLlm:
    return ScriptedLlm(model="gemini-scripted", script=list(script), requests=[])


EXTRACTED = ExtractedDocument(
    document_type="salary_letter",
    subject_name="Orrin Talvek Drossmere",
    currency="SGD",
    principal_amount=198000.0,
    sow_category="employment_income",
    evidence=[{"field": "principal_amount", "quote": "Annual remuneration is SGD 198,000.00"}],
).model_dump_json()

GOOD_REPORT = "Disposition: escalate_edd. Annual pay SGD 198,000.00."


@pytest.fixture
def wired(monkeypatch):
    """Canned verify() and sanctions results, and a fresh fake model per agent."""

    def canned_verify(doc):
        return VerificationResult(
            document_type=doc.document_type,
            overall="verified",
            fields=[FieldCheck("subject_name", "verified")],
            reasons=[],
        )

    async def canned_lookup(name, tool_context):
        return {
            "query": name,
            "matches": [{"name": "Listed", "list_name": "SYNTHETIC", "score": 0.9}],
            "checked_at": "2026-11-02T00:00:00Z",
        }

    monkeypatch.setattr(review_tools, "verify", canned_verify)
    monkeypatch.setattr(agent_module, "_lookup_sanctions", canned_lookup)

    def install(coordinator, extraction=(), writer=(), critic=()):
        monkeypatch.setattr(agent_module.coordinator, "model", coordinator)
        monkeypatch.setattr(agent_module.extraction_agent, "model", fake(*extraction))
        monkeypatch.setattr(agent_module.report_writer, "model", fake(*writer))
        monkeypatch.setattr(agent_module.critic, "model", fake(*critic))
        return agent_module

    return install


async def run(runner, session_id, message):
    events = []
    async for event in runner.run_async(user_id="u", session_id=session_id, new_message=message):
        events.append(event)
    return events


def user_text(text):
    return types.Content(role="user", parts=[types.Part(text=text)])


def coordinator_script(after_critic="done"):
    return fake(
        ("extraction_agent", {"request": "document"}),
        ("run_verify", {}),
        ("run_screening", {}),
        ("compute_disposition", {}),
        ("critic_loop", {"request": "review"}),
        ("escalate_to_edd", {"reason": "Alias match on the synthetic list."}),
        after_critic,
    )


async def _session(agent):
    runner = InMemoryRunner(agent=agent, app_name="sow_reviewer")
    session = await runner.session_service.create_session(app_name="sow_reviewer", user_id="u")
    return runner, session


async def test_full_flow_pauses_for_human_then_escalates(wired):
    mod = wired(
        coordinator_script(),
        extraction=[EXTRACTED],
        writer=[GOOD_REPORT],
        critic=[("check_citations", {}), ("approve_report", {})],
    )
    runner, session = await _session(mod.root_agent)

    events = await run(runner, session.id, user_text(DOCUMENT))

    # The model said request="document"; extraction received the raw document.
    extraction_request = mod.extraction_agent.model.requests[0]
    assert DOCUMENT in str(extraction_request.contents)

    calls = [c for e in events for c in e.get_function_calls()]
    confirmation = next(c for c in calls if c.name == CONFIRM)
    state = (await runner.session_service.get_session(app_name="sow_reviewer", user_id="u", session_id=session.id)).state
    assert state["disposition"] == "escalate_edd"
    assert state["critic_approved"] is True
    assert state["citation_check"]["ok"] is True
    assert not state.get("edd_escalation"), "must not escalate before a human confirms"

    reply = types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=confirmation.id,
                    name=CONFIRM,
                    response=ToolConfirmation(confirmed=True).model_dump(by_alias=True),
                )
            )
        ],
    )
    await run(runner, session.id, reply)
    state = (await runner.session_service.get_session(app_name="sow_reviewer", user_id="u", session_id=session.id)).state
    assert state["edd_escalation"]["status"] == "escalated_to_edd"


async def test_human_rejection_does_not_escalate(wired):
    mod = wired(
        coordinator_script("rejected"),
        extraction=[EXTRACTED],
        writer=[GOOD_REPORT],
        critic=[("check_citations", {}), ("approve_report", {})],
    )
    runner, session = await _session(mod.root_agent)
    events = await run(runner, session.id, user_text(DOCUMENT))
    confirmation = next(c for e in events for c in e.get_function_calls() if c.name == CONFIRM)

    reply = types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=confirmation.id,
                    name=CONFIRM,
                    response=ToolConfirmation(confirmed=False).model_dump(by_alias=True),
                )
            )
        ],
    )
    await run(runner, session.id, reply)
    state = (await runner.session_service.get_session(app_name="sow_reviewer", user_id="u", session_id=session.id)).state
    assert not state.get("edd_escalation")


async def test_critic_loop_stops_after_two_iterations(wired):
    mod = wired(
        fake(
            ("extraction_agent", {"request": "document"}),
            ("run_verify", {}),
            ("run_screening", {}),
            ("compute_disposition", {}),
            ("critic_loop", {"request": "review"}),
            "done",
        ),
        extraction=[EXTRACTED],
        writer=["first draft, 999,999", "second draft, 999,999", "never used"],
        critic=[
            ("check_citations", {}),
            "1. Amount 999,999 is not in the document.",
            ("check_citations", {}),
            "1. Amount 999,999 is still not in the document.",
            "never used",
        ],
    )
    runner, session = await _session(mod.root_agent)
    await run(runner, session.id, user_text(DOCUMENT))

    assert len(mod.report_writer.model.requests) == 2
    # The second draft was told what the critic said.
    assert "not in the document" in str(mod.report_writer.model.requests[1].config.system_instruction)
    state = (await runner.session_service.get_session(app_name="sow_reviewer", user_id="u", session_id=session.id)).state
    assert state["critic_approved"] is False
    assert state["citation_check"]["unsupported_amounts"] == [999999.0]


async def test_critic_cannot_approve_a_failing_check(wired):
    mod = wired(
        fake(
            ("extraction_agent", {"request": "document"}),
            ("run_verify", {}),
            ("run_screening", {}),
            ("compute_disposition", {}),
            ("critic_loop", {"request": "review"}),
            "done",
        ),
        extraction=[EXTRACTED],
        writer=["draft with 999,999", "draft with 999,999"],
        critic=[
            ("check_citations", {}),
            ("approve_report", {}),
            "gave up",
            ("check_citations", {}),
            "gave up",
        ],
    )
    runner, session = await _session(mod.root_agent)
    await run(runner, session.id, user_text(DOCUMENT))
    state = (await runner.session_service.get_session(app_name="sow_reviewer", user_id="u", session_id=session.id)).state
    assert state["critic_approved"] is False


async def test_failed_sanctions_lookup_blocks_the_disposition(wired, monkeypatch):
    mod = wired(
        fake(
            ("extraction_agent", {"request": "document"}),
            ("run_verify", {}),
            ("run_screening", {}),
            ("compute_disposition", {}),
            "stopped",
        ),
        extraction=[EXTRACTED],
    )

    async def failing(name, tool_context):
        return {"isError": True, "content": [{"type": "text", "text": "boom"}]}

    monkeypatch.setattr(agent_module, "_lookup_sanctions", failing)
    runner, session = await _session(mod.root_agent)
    events = await run(runner, session.id, user_text(DOCUMENT))
    responses = [r for e in events for r in e.get_function_responses() if r.name == "compute_disposition"]
    assert "error" in responses[0].response
    state = (await runner.session_service.get_session(app_name="sow_reviewer", user_id="u", session_id=session.id)).state
    assert state["sanctions_results"][0]["status"] == "unavailable"
    assert not state.get("disposition")


def test_the_model_sees_run_screening_and_not_the_raw_lookup():
    names = [getattr(t, "name", getattr(t, "__name__", "")) for t in agent_module.coordinator.tools]
    assert "run_screening" in names
    assert "sanctions_lookup" not in names
    assert agent_module.sanctions_toolset not in agent_module.coordinator.tools


WITH_COUNTERPARTY = {"subject_name": "Orrin Talvek Drossmere", "counterparty": "Quillstone Marine Services Pte Ltd"}
CHECKED = {"status": "checked", "matches": []}


def _context(**state):
    return SimpleNamespace(state=dict(state))


@pytest.mark.parametrize(
    "extracted,want",
    [
        (WITH_COUNTERPARTY, ["Orrin Talvek Drossmere", "Quillstone Marine Services Pte Ltd"]),
        ({"subject_name": "Lim Hui Ling", "counterparty": None}, ["Lim Hui Ling"]),
        ({"subject_name": "Lim Hui Ling", "counterparty": "  "}, ["Lim Hui Ling"]),
        ({"subject_name": "Lim Hui Ling", "counterparty": "Lim Hui Ling"}, ["Lim Hui Ling"]),
    ],
)
def test_screening_names_come_from_the_extraction(extracted, want):
    assert review_tools.screening_names(extracted) == want


def test_disposition_refused_when_the_counterparty_was_not_screened():
    ctx = _context(
        verification={"overall": "verified", "reasons": []},
        extracted=WITH_COUNTERPARTY,
        sanctions_results=[{**CHECKED, "query": "Orrin Talvek Drossmere"}],
    )
    result = review_tools.compute_disposition(ctx)
    assert "Quillstone Marine Services Pte Ltd" in result["error"]
    assert "disposition" not in ctx.state


def test_disposition_refused_when_any_lookup_was_unavailable():
    ctx = _context(
        verification={"overall": "verified", "reasons": []},
        extracted=WITH_COUNTERPARTY,
        sanctions_results=[
            {**CHECKED, "query": "Orrin Talvek Drossmere"},
            {"status": "unavailable", "matches": [], "query": "Quillstone Marine Services Pte Ltd"},
        ],
    )
    assert "unavailable" in review_tools.compute_disposition(ctx)["error"]


def test_disposition_given_when_every_name_was_checked():
    ctx = _context(
        verification={"overall": "verified", "reasons": []},
        extracted=WITH_COUNTERPARTY,
        sanctions_results=[{**CHECKED, "query": n} for n in review_tools.screening_names(WITH_COUNTERPARTY)],
    )
    assert review_tools.compute_disposition(ctx)["disposition"] == "evidenced"


async def test_screen_runs_the_lookups_concurrently():
    started, both_started = [], asyncio.Event()

    async def lookup(name):
        started.append(name)
        if len(started) == 2:
            both_started.set()
        # Times out unless the second lookup starts while this one is still waiting.
        await asyncio.wait_for(both_started.wait(), timeout=2)
        return {"query": name, "matches": [], "checked_at": "t"}

    ctx = _context(extracted=WITH_COUNTERPARTY)
    await review_tools.screen(ctx, lookup)
    assert [r["query"] for r in ctx.state["sanctions_results"]] == review_tools.screening_names(WITH_COUNTERPARTY)
    assert {r["status"] for r in ctx.state["sanctions_results"]} == {"checked"}


async def test_screen_records_a_raising_lookup_as_unavailable():
    async def lookup(name):
        if name.startswith("Quillstone"):
            raise ConnectionError("list down")
        return {"query": name, "matches": [], "checked_at": "t"}

    ctx = _context(extracted=WITH_COUNTERPARTY)
    await review_tools.screen(ctx, lookup)
    assert [r["status"] for r in ctx.state["sanctions_results"]] == ["checked", "unavailable"]


@pytest.mark.parametrize(
    "overall,results,want",
    [
        ("verified", [{"matches": []}], "evidenced"),
        ("needs_follow_up", [{"matches": []}], "follow_up"),
        ("insufficient", [{"matches": []}], "insufficient"),
        ("verified", [{"matches": []}, {"matches": [{"score": 0.7}]}], "escalate_edd"),
        ("insufficient", [{"matches": [{"score": 1.0}]}], "escalate_edd"),
    ],
)
def test_disposition_table(overall, results, want):
    assert review_tools.disposition_for(overall, results) == want


@pytest.mark.parametrize(
    "response,matches",
    [
        ({"isError": True, "content": [{"type": "text", "text": "boom"}]}, None),
        ({"structuredContent": {"matches": []}}, []),
        ({"content": [{"type": "text", "text": '{"matches": [{"score": 1.0}]}'}]}, [{"score": 1.0}]),
        ({"content": [{"type": "text", "text": "not json"}]}, None),
        ("garbage", None),
    ],
)
def test_parse_sanctions_response(response, matches):
    parsed = review_tools.parse_sanctions_response(response)
    assert (parsed["matches"] if parsed else None) == matches


def _clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GOOGLE_", "RAG_"))}
    return {**env, **extra}


def test_import_needs_no_credentials():
    subprocess.run(
        [sys.executable, "-c", "import sow_reviewer.agent as a; assert a.policy_agent is None; assert a.root_agent.name == 'coordinator'"],
        cwd=ROOT, env=_clean_env(), check=True,
    )


def test_policy_agent_appears_only_with_rag_corpus():
    code = textwrap.dedent(
        """
        import sow_reviewer.agent as a
        assert a.policy_agent is not None
        assert a.policy_agent.tools[0].name == 'retrieve_sow_policy'
        assert 'policy_agent' in [getattr(t, 'name', '') for t in a.coordinator.tools]
        """
    )
    subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, check=True,
        env=_clean_env(RAG_CORPUS="projects/p/locations/l/ragCorpora/1"),
    )


LIST_MCP_TOOLS = textwrap.dedent(
    """
    import asyncio
    import sow_reviewer.agent as a
    async def main():
        tools = await a.sanctions_toolset.get_tools()
        print(sorted(t.name for t in tools))
        await a.sanctions_toolset.close()
    asyncio.run(main())
    """
)


def test_mcp_server_starts_over_stdio_and_lists_the_tool():
    out = subprocess.run(
        [sys.executable, "-c", LIST_MCP_TOOLS], cwd=ROOT, env=_clean_env(),
        check=True, capture_output=True, text=True, timeout=90,
    ).stdout
    assert "sanctions_lookup" in out


def test_agent_and_mcp_work_in_the_deployed_layout(tmp_path):
    """Mirrors the image: /app/agents/sow_reviewer, /app/data, /app/tools, cwd /app."""
    config = json.loads((ROOT / "sow_reviewer" / ".agent_engine_config.json").read_text())
    assert config["extra_packages"] == ["../data", "../tools"]

    ignore = shutil.ignore_patterns("__pycache__", ".env")
    shutil.copytree(ROOT / "sow_reviewer", tmp_path / "agents" / "sow_reviewer", ignore=ignore)
    shutil.copytree(ROOT / "data", tmp_path / "data", ignore=ignore)
    shutil.copytree(ROOT / "tools", tmp_path / "tools", ignore=ignore)

    out = subprocess.run(
        [sys.executable, "-c", "import sow_reviewer.agent as a; print(a.ROOT)\n" + LIST_MCP_TOOLS],
        cwd=tmp_path, env=_clean_env(PYTHONPATH=str(tmp_path / "agents")),
        check=True, capture_output=True, text=True, timeout=90,
    ).stdout
    assert str(tmp_path.resolve()) in out
    assert "sanctions_lookup" in out


def test_planted_issues_stay_out_of_the_extraction_prompt():
    instruction = agent_module.extraction_agent.instruction
    for phrase in ("cash deposit 60,000", "60,000 现金", "赠与人资金来源只有一句话", "埋的点"):
        assert phrase not in instruction


STUB_SERVER = """
from mcp.server.mcpserver import MCPServer
server = MCPServer("stub")

@server.tool()
def sanctions_lookup(name: str) -> dict:
    \"\"\"Canned response for testing transport and parsing only.\"\"\"
    {body}

server.run(transport="stdio")
"""


def _stub_toolset(tmp_path, body):
    from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
    from mcp import StdioServerParameters

    script = tmp_path / "stub_server.py"
    script.write_text(STUB_SERVER.format(body=body))
    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(command=sys.executable, args=[str(script)]),
            timeout=30.0,
        )
    )


def _flow_to_disposition():
    return fake(
        ("extraction_agent", {"request": "document"}),
        ("run_verify", {}),
        ("run_screening", {}),
        ("compute_disposition", {}),
        "stopped",
    )


async def test_real_mcp_result_reaches_the_disposition(wired, monkeypatch, tmp_path):
    canned = '''return {"query": name, "matches": [{"name": "Listed", "list_name": "SYNTHETIC", "score": 0.9}], "checked_at": "t"}'''
    toolset = _stub_toolset(tmp_path, canned)
    mod = wired(_flow_to_disposition(), extraction=[EXTRACTED])
    # The real lookup, through a stub server: wired() replaced it with a canned one.
    monkeypatch.setattr(mod, "_lookup_sanctions", REAL_LOOKUP)
    monkeypatch.setattr(mod, "sanctions_toolset", toolset)
    runner, session = await _session(mod.root_agent)
    try:
        await run(runner, session.id, user_text(DOCUMENT))
        state = (await runner.session_service.get_session(app_name="sow_reviewer", user_id="u", session_id=session.id)).state
    finally:
        await runner.close()
        await toolset.close()
    assert state["sanctions_results"][0]["matches"][0]["score"] == 0.9
    assert state["disposition"] == "escalate_edd"


async def test_real_mcp_tool_error_blocks_the_disposition(wired, monkeypatch, tmp_path):
    toolset = _stub_toolset(tmp_path, 'raise RuntimeError("boom")')
    mod = wired(_flow_to_disposition(), extraction=[EXTRACTED])
    # The real lookup, through a stub server: wired() replaced it with a canned one.
    monkeypatch.setattr(mod, "_lookup_sanctions", REAL_LOOKUP)
    monkeypatch.setattr(mod, "sanctions_toolset", toolset)
    runner, session = await _session(mod.root_agent)
    try:
        await run(runner, session.id, user_text(DOCUMENT))
        state = (await runner.session_service.get_session(app_name="sow_reviewer", user_id="u", session_id=session.id)).state
    finally:
        await runner.close()
        await toolset.close()
    assert state["sanctions_results"][0]["status"] == "unavailable"
    assert not state.get("disposition")
