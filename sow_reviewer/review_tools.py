"""Tools and callbacks for the coordinator.

Session state keys written here:
  document_text     raw text of the document under review
  verification      verify() output as a dict
  sanctions_results one entry per screened name, written by screen()
  disposition       evidenced | follow_up | insufficient | escalate_edd
  citation_check    last result of check_citations
  critic_approved   True once the critic approved the draft
  edd_escalation    set only after a human confirms escalation
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from google.adk.tools.exit_loop_tool import exit_loop
from google.adk.tools.tool_context import ToolContext

from . import citations
from .schemas import ExtractedDocument
from .verify import verify

_OVERALL_TO_DISPOSITION = {
    "verified": "evidenced",
    "needs_follow_up": "follow_up",
    "insufficient": "insufficient",
}


def disposition_for(overall: str, sanctions_results: list[dict]) -> str:
    """Any sanctions match escalates. Otherwise the verify() grade decides."""
    if any(result.get("matches") for result in sanctions_results):
        return "escalate_edd"
    return _OVERALL_TO_DISPOSITION[overall]


def run_verify(tool_context: ToolContext) -> dict:
    """Runs the deterministic rules on the extraction already stored in session state."""
    extracted = tool_context.state.get("extracted")
    if not extracted:
        return {"error": "No extraction in session state. Call extraction_agent first."}
    result = dataclasses.asdict(verify(ExtractedDocument.model_validate(extracted)))
    tool_context.state["verification"] = result
    return result


def screening_names(extracted: dict) -> list[str]:
    """The names every review must screen: the client, and the counterparty when there is one."""
    names: list[str] = []
    for name in (extracted.get("subject_name"), extracted.get("counterparty")):
        name = (name or "").strip()
        if name and name not in names:
            names.append(name)
    return names


SANCTIONS_SOURCE = "synthetic sanctions list (tools/sanctions_server.py, over MCP)"


async def screen(tool_context: ToolContext, lookup: Callable[[str], Awaitable[Any]]) -> dict:
    """Screens every name from the extraction concurrently and stores one result per name.

    The names come from session state, not from the model, so a name cannot be retyped
    or left out. A lookup that raises or returns an error is recorded as unavailable.
    """
    state = tool_context.state
    extracted = state.get("extracted")
    if not extracted:
        return {"error": "No extraction in session state. Call extraction_agent first."}
    names = screening_names(extracted)
    responses = await asyncio.gather(*(lookup(name) for name in names), return_exceptions=True)
    results = []
    for name, response in zip(names, responses):
        parsed = None if isinstance(response, BaseException) else parse_sanctions_response(response)
        results.append(
            {
                "query": name,
                "source": SANCTIONS_SOURCE,
                "status": "checked" if parsed is not None else "unavailable",
                "matches": parsed["matches"] if parsed is not None else [],
                "checked_at": (parsed or {}).get("checked_at") or datetime.now(timezone.utc).isoformat(),
            }
        )
    state["sanctions_results"] = results
    return {"results": results}


def compute_disposition(tool_context: ToolContext) -> dict:
    """Combines the verify() grade and the sanctions results into the final disposition."""
    state = tool_context.state
    verification = state.get("verification")
    if not verification:
        return {"error": "No verification result. Call run_verify first."}
    results = state.get("sanctions_results")
    if not results:
        return {"error": "No sanctions screening recorded. Call run_screening first."}
    unavailable = [r["query"] for r in results if r.get("status") != "checked"]
    if unavailable:
        return {"error": f"Screening was unavailable for {unavailable}. No disposition can be given without screening."}
    # Every name the extraction requires must have been checked, not just one of them.
    missing = sorted(set(screening_names(state.get("extracted") or {})) - {r["query"] for r in results})
    if missing:
        return {"error": f"Not screened: {missing}. No disposition can be given without screening every name."}
    disposition = disposition_for(verification["overall"], results)
    state["disposition"] = disposition
    return {"disposition": disposition, "reasons": verification.get("reasons", [])}


def check_citations(tool_context: ToolContext) -> dict:
    """Checks that quotes, amounts and policy clauses in the draft report exist in their sources."""
    state = tool_context.state
    report = state.get("draft_report") or ""
    document = state.get("document_text") or ""
    extracted = state.get("extracted") or {}
    policy_answer = state.get("policy_answer") or ""
    verification = state.get("verification") or {}

    known = [t["amount"] for t in extracted.get("transactions", [])]
    if extracted.get("principal_amount") is not None:
        known.append(extracted["principal_amount"])

    disposition = state.get("disposition") or ""
    result = {
        "unsupported_quotes": citations.unsupported_quotes(
            [e["quote"] for e in extracted.get("evidence", [])], document
        ),
        "unsupported_amounts": citations.unsupported_amounts(
            report, [document, policy_answer, " ".join(verification.get("reasons", []))], known
        ),
        "unsupported_clauses": citations.unsupported_clauses(report, policy_answer),
        "disposition_stated": bool(disposition) and disposition in report,
    }
    result["ok"] = result["disposition_stated"] and not any(
        result[k] for k in ("unsupported_quotes", "unsupported_amounts", "unsupported_clauses")
    )
    state["citation_check"] = result
    return result


def approve_report(tool_context: ToolContext) -> dict | None:
    """Approves the draft and ends the critic loop. Refused unless check_citations passed."""
    check = tool_context.state.get("citation_check") or {}
    if not check.get("ok"):
        return {"error": "check_citations has not passed. List the required fixes instead."}
    tool_context.state["critic_approved"] = True
    exit_loop(tool_context)
    return None


def escalate_to_edd(reason: str, tool_context: ToolContext) -> dict:
    """Escalates this review to Enhanced Due Diligence. A human must confirm before this runs."""
    disposition = tool_context.state.get("disposition")
    if disposition != "escalate_edd":
        return {"error": f"Disposition is {disposition!r}; only escalate_edd may be escalated."}
    record = {"status": "escalated_to_edd", "reason": reason}
    tool_context.state["edd_escalation"] = record
    return record


def capture_document(callback_context) -> None:
    """Keeps the raw user text, so downstream steps read the original and not a retyped copy."""
    content = callback_context.user_content
    text = "".join(p.text or "" for p in (content.parts or [])) if content else ""
    state = callback_context.state
    # A confirmation reply carries no text, and on that resume ADK reports the original
    # message as user_content. Resetting here would wipe the review being confirmed.
    if not text.strip() or text == state.get("document_text"):
        return None
    state["document_text"] = text
    for key, empty in (
        ("extracted", None),
        ("verification", None),
        ("sanctions_results", []),
        ("disposition", None),
        ("policy_answer", None),
        ("draft_report", None),
        ("critic_feedback", None),
        ("citation_check", None),
        ("critic_approved", False),
        ("edd_escalation", None),
    ):
        state[key] = empty
    return None


def inject_document(tool, args: dict[str, Any], tool_context: ToolContext) -> None:
    """Replaces the model's `request` with the raw document, so quotes stay verbatim."""
    if tool.name == "extraction_agent":
        args["request"] = tool_context.state.get("document_text") or args.get("request", "")
    return None


def parse_sanctions_response(response: Any) -> dict | None:
    """Reads the tool's dict out of an MCP result. None means the lookup did not succeed."""
    if not isinstance(response, dict) or response.get("isError") or response.get("is_error"):
        return None
    structured = response.get("structuredContent") or response.get("structured_content")
    if isinstance(structured, dict) and "matches" in structured:
        return structured
    if "matches" in response:
        return response
    for part in response.get("content") or []:
        try:
            parsed = json.loads(part.get("text", ""))
        except (AttributeError, TypeError, ValueError):
            continue
        if isinstance(parsed, dict) and "matches" in parsed:
            return parsed
    return None


def record_tool_result(tool, args: dict[str, Any], tool_context: ToolContext, tool_response: Any) -> dict | None:
    """Hands the coordinator the critic's outcome."""
    state = tool_context.state
    if tool.name == "critic_loop":
        # An approved loop ends on exit_loop, which has no text, so the raw tool result is empty.
        approved = bool(state.get("critic_approved"))
        return {
            "report": state.get("draft_report"),
            "critic_approved": approved,
            "citation_check": state.get("citation_check"),
            "open_feedback": None if approved else state.get("critic_feedback"),
        }
    return None
