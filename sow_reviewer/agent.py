"""SoW Reviewer agents.

coordinator (root_agent)
  extraction_agent   AgentTool, structured output, gets the raw document
  run_verify         deterministic rules, sow_reviewer/verify.py
  run_screening      code screens every extracted name at once; each lookup goes
                     over MCP (stdio) to tools/sanctions_server.py
  compute_disposition
  policy_agent       AgentTool, RAG Engine; only exists when RAG_CORPUS is set
  critic_loop        report_writer + critic, at most 2 iterations
  escalate_to_edd    require_confirmation=True, ADK pauses for a human

Sub-agents are AgentTools rather than transfer targets because a transfer does
not return to the coordinator, and this flow has steps after each of them.
Importing this module needs no credentials: the model client and the MCP
subprocess are both created on first use.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from google.adk.agents.llm_agent import LlmAgent
from google.adk.agents.loop_agent import LoopAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models.google_llm import Gemini
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from google.adk.tools.retrieval.vertex_ai_rag_retrieval import VertexAiRagRetrieval
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from mcp import StdioServerParameters

from .review_tools import (
    approve_report,
    capture_document,
    check_citations,
    compute_disposition,
    escalate_to_edd,
    inject_document,
    record_tool_result,
    run_verify,
    screen,
)
from .schemas import ExtractedDocument

MODEL = os.environ.get("SOW_MODEL", "gemini-3.8-flash")
# A 503 during a demand spike is normal here. Retries sit on the model, so every agent gets them.
LLM = Gemini(model=MODEL, retry_options=types.HttpRetryOptions(attempts=6, initial_delay=5, max_delay=60))

# Locally data/ sits beside sow_reviewer/. In the Agent Engine image the agent is
# at /app/agents/sow_reviewer and extra_packages puts data/ and tools/ at /app.
ROOT = next(p for p in Path(__file__).resolve().parents[1:3] if (p / "data" / "field_spec.md").exists())

_FIELD_SPEC = (ROOT / "data" / "field_spec.md").read_text(encoding="utf-8")

EXTRACTION_INSTRUCTION = f"""You are the extraction step of a source-of-wealth (SoW) review for a private bank.

The user message contains ONE document (a bank statement, share sale agreement, trust deed, salary letter or gift letter) as plain text.

Your job is to extract evidence, not to judge it. Rules:
1. Fill every field only from what the document literally says. If a field is not in the document, output null. Never guess, never infer from world knowledge.
2. For every non-null field, add an `evidence` entry whose `quote` is a verbatim excerpt (20-200 characters) from the document that supports it.
3. Amounts: strip currency symbols and thousands separators (S$2,750,000.00 -> 2750000). Put the ISO code in `currency`.
4. Dates: YYYY-MM-DD. If only month and year are given, use the 1st and note it in `missing_or_unclear`.
5. `transactions`: only for bank statements. Include every single credit or debit >= 10,000, plus any line mentioning salary, consideration, distribution, gift or sale. Skip small everyday spending.
6. `missing_or_unclear`: list every required field you could not fill, every contradiction (e.g. two currencies, two different dates for the same event), and anything whose origin the document asserts but does not support (e.g. a cash deposit with no stated source, a donor's funds described in one sentence with no supporting document). Write each as one short English sentence.
7. Keep names and descriptions in the document's original language. Do not translate.
8. Do not comment on whether the source of wealth is acceptable. That decision is made by deterministic rules downstream, not by you.

Field specification (authoritative):

{_FIELD_SPEC}
"""

extraction_agent = LlmAgent(
    name="extraction_agent",
    model=LLM,
    description="Extracts structured fields and verbatim evidence from one SoW document.",
    instruction=EXTRACTION_INSTRUCTION,
    output_schema=ExtractedDocument,
    output_key="extracted",
    include_contents="none",
)

sanctions_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        # sys.executable, not `uv run`: the deployed image has no uv.
        server_params=StdioServerParameters(
            command=sys.executable,
            args=[str(ROOT / "tools" / "sanctions_server.py")],
            cwd=str(ROOT),
        ),
        timeout=30.0,
    ),
    tool_filter=["sanctions_lookup"],
)


async def _lookup_sanctions(name: str, tool_context: ToolContext):
    """One lookup through the MCP server. The caller is code, not the model."""
    tools = await sanctions_toolset.get_tools()
    tool = next(t for t in tools if t.name == "sanctions_lookup")
    return await tool.run_async(args={"name": name}, tool_context=tool_context)


async def run_screening(tool_context: ToolContext) -> dict:
    """Screens the client and the counterparty from the extraction against the sanctions list."""
    return await screen(tool_context, lambda name: _lookup_sanctions(name, tool_context))


def _build_policy_agent() -> LlmAgent | None:
    corpus = os.environ.get("RAG_CORPUS")
    if not corpus:
        return None
    return LlmAgent(
        name="policy_agent",
        model=LLM,
        description="Answers questions about the source-of-wealth policy, with clause citations.",
        instruction=(
            "Answer only from the retrieved policy text. Cite every rule by its clause reference "
            "exactly as written, for example SOW-POL 4.2, and state thresholds exactly as written. "
            "If the retrieved text does not answer the question, say that it does not."
        ),
        # Kept apart from the coordinator: this is a Gemini built-in retrieval tool, and the
        # coordinator carries function tools.
        tools=[
            VertexAiRagRetrieval(
                name="retrieve_sow_policy",
                description="Retrieves passages from the internal SoW policy and AML/CFT guidance.",
                rag_resources=[{"rag_corpus": corpus}],
                similarity_top_k=5,
            )
        ],
        output_key="policy_answer",
    )


policy_agent = _build_policy_agent()


def _review_context(ctx: ReadonlyContext) -> str:
    keys = ("disposition", "verification", "sanctions_results", "extracted", "policy_answer")
    return json.dumps({k: ctx.state.get(k) for k in keys}, ensure_ascii=False, indent=2)


def _writer_instruction(ctx: ReadonlyContext) -> str:
    feedback = ctx.state.get("critic_feedback")
    revise = f"\n\nA critic rejected your last draft. Fix exactly this:\n{feedback}" if feedback else ""
    return f"""Write the reviewer's report for one source-of-wealth document, in English, under 250 words.

Sections: Disposition, Key figures, Findings, Sanctions screening, Policy references.
- State the disposition using its exact value. You do not decide it and may not soften it.
- Findings come from the verification reasons and `missing_or_unclear`. Quote evidence verbatim.
- Use only amounts present in the context below. Write no derived figure unless it is `principal_amount`.
- Cite a policy clause only if it appears in `policy_answer`. If that is null, write "Policy check not run."{revise}

Review context:
{_review_context(ctx)}"""


def _critic_instruction(ctx: ReadonlyContext) -> str:
    return f"""You check a draft report. You do not rewrite it.

1. Call check_citations.
2. Read the draft against the context below: does each finding match the verification reasons, and is each amount attached to the right item?
3. If check_citations returned ok=true and step 2 found nothing, call approve_report and output nothing else.
4. Otherwise output a short numbered list of required fixes and do not call approve_report.

Draft:
{ctx.state.get("draft_report")}

Review context:
{_review_context(ctx)}"""


report_writer = LlmAgent(
    name="report_writer",
    model=LLM,
    description="Drafts the reviewer's report from session state.",
    instruction=_writer_instruction,
    include_contents="none",
    output_key="draft_report",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)

critic = LlmAgent(
    name="critic",
    model=LLM,
    description="Checks the draft's citations and amounts against their sources.",
    instruction=_critic_instruction,
    include_contents="none",
    tools=[check_citations, approve_report],
    output_key="critic_feedback",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)

# LoopAgent is marked deprecated in favour of Workflow, but ADK 2.13 says Workflow
# cannot yet sit under an LlmAgent, and the coordinator is an LlmAgent.
critic_loop = LoopAgent(
    name="critic_loop",
    description="Writes the report and checks its citations and amounts, at most twice.",
    sub_agents=[report_writer, critic],
    max_iterations=2,
)


def _coordinator_instruction(ctx: ReadonlyContext) -> str:
    if policy_agent is None:
        policy_step = "5. No policy tool is configured in this environment. Skip this step."
        question = "say that the policy tool is not configured in this environment"
    else:
        policy_step = (
            "5. If the disposition is follow_up or insufficient, call policy_agent and ask what the "
            "policy requires for this situation."
        )
        question = "call policy_agent and relay its answer with the clause reference"
    return f"""You coordinate a source-of-wealth review for a private bank's reviewer. All data is synthetic.

If the user sends a document, run these steps in order, one at a time:
1. extraction_agent with request="document". The system supplies the document itself.
2. run_verify.
3. run_screening. It takes no arguments: it screens every name from the extraction at once.
4. compute_disposition.
{policy_step}
6. critic_loop with request="review".
7. If the disposition is escalate_edd, call escalate_to_edd with a one-sentence reason. A human must approve it.

Then reply with the report returned by critic_loop, unchanged. If critic_approved is false, say above it that the critic's open items are unresolved and list them.

Rules:
- You never decide the disposition. It comes only from compute_disposition.
- If a tool returns an error, stop and report the error. Never guess a result or give a disposition without screening.
- If escalate_to_edd is rejected, say so and state that the case stays open for the reviewer.

If the user asks a policy question and sends no document, {question}."""


_coordinator_tools = [
    AgentTool(extraction_agent),
    run_verify,
    run_screening,
    compute_disposition,
    AgentTool(critic_loop),
    FunctionTool(escalate_to_edd, require_confirmation=True),
]
if policy_agent is not None:
    _coordinator_tools.append(AgentTool(policy_agent))

coordinator = LlmAgent(
    name="coordinator",
    model=LLM,
    description="Runs one source-of-wealth document review end to end.",
    instruction=_coordinator_instruction,
    tools=_coordinator_tools,
    before_agent_callback=capture_document,
    before_tool_callback=inject_document,
    after_tool_callback=record_tool_result,
)

root_agent = coordinator
