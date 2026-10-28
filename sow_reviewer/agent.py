"""SoW Reviewer agents.

Day 3 (2026-10-28): extraction agent only. root_agent points at it so
`adk run sow_reviewer` / `adk web .` work. Later days add coordinator,
policy-retrieval sub-agent, critic loop and the sanctions MCP tool.
"""

from __future__ import annotations

from pathlib import Path

from google.adk.agents.llm_agent import LlmAgent

from .schemas import ExtractedDocument

MODEL = "gemini-3.8-flash"

_FIELD_SPEC = (Path(__file__).resolve().parent.parent / "data" / "field_spec.md").read_text(encoding="utf-8")

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
    model=MODEL,
    description="Extracts structured fields and verbatim evidence from one SoW document.",
    instruction=EXTRACTION_INSTRUCTION,
    output_schema=ExtractedDocument,
    output_key="extracted",
    include_contents="none",
)

root_agent = extraction_agent
