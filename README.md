# SoW Reviewer

Multi-agent **source-of-wealth document review** built on Google ADK + Gemini. Personal project, synthetic data only. Written for a Google Cloud FDE application; see `docs/` for design notes.

**Status (2026-10-28):** extraction agent done, not yet run end-to-end (needs an API key). Verification rules, sanctions MCP tool, RAG sub-agent, critic loop, evals and deployment follow the schedule below.

## What it does

A coordinator agent reviews one SoW document pack and decides whether the stated source of wealth is evidenced, needs follow-up, or should be escalated to Enhanced Due Diligence (EDD), with a human confirming the escalation.

```
coordinator
├── extraction_agent      Gemini structured output → ExtractedDocument (fields + verbatim evidence)
├── verify()              hand-written rules, no model: field status + overall grade
├── sanctions_lookup      MCP tool (stdio), synthetic list
├── policy_agent          RAG Engine over public AML/CFT guidance + an internal SoW policy
└── critic loop (≤2)      checks citations and amounts, then human confirmation before EDD
```

Design principle: **the model extracts evidence, deterministic rules decide.** Every field in the extraction carries a verbatim quote, so a reviewer can trace each conclusion back to the document.

## Run locally

```bash
cp .env.example sow_reviewer/.env   # put your AI Studio key in GOOGLE_API_KEY
cp .env.example .env
uv sync
uv run adk run sow_reviewer                # chat: paste a document from data/cases/
uv run adk web .                           # browser UI
uv run python scripts/extract_all.py       # extract all 5 cases → outputs/extracted/
uv run pytest
```

## Layout

| Path | What |
|---|---|
| `sow_reviewer/agent.py` | ADK agents (`root_agent` for the CLI) |
| `sow_reviewer/schemas.py` | Pydantic output schema |
| `sow_reviewer/verify.py` | hand-written verification rules |
| `tools/sanctions_server.py` | hand-written MCP server |
| `data/cases/` | 5 synthetic documents |
| `data/expected/` | ground truth per case |
| `data/field_spec.md` | what to extract, and the planted issues per case |
| `docs/verify_rules.md` | rule table for `verify()` and the MCP tool spec |
| `scripts/extract_all.py` | batch extraction with latency and token counts |

## Schedule

| Day | Deliverable |
|---|---|
| 10-26 | GCP project, `adk` installed, template runs |
| 10-27 | 5 synthetic cases + field spec |
| 10-28 | extraction agent, 5 JSON outputs |
| 10-29 | hand-written `verify()` + `sanctions_lookup`, unit tests |
| 10-30 | policy corpus → RAG Engine, retrieval sub-agent |
| 10-31 | coordinator + critic loop + human confirmation + `adk eval` on 10 golden cases |
| 11-01 | tracing, p50/p95 and cost per case, deploy to Agent Runtime, demo |

## Boundaries

All documents, names, companies and list entries are fictional. Nothing here is derived from any employer's code, specifications or client material.
