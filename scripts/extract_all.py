"""Run the extraction agent over every case in data/cases and write JSON to outputs/extracted/.

Usage:
    uv run python scripts/extract_all.py            # all cases
    uv run python scripts/extract_all.py sow-003    # one case

Needs GOOGLE_API_KEY in .env (project root) or the environment.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from google.adk.runners import InMemoryRunner
from google.genai import types

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "sow_reviewer" / ".env")

from sow_reviewer.agent import extraction_agent  # noqa: E402
from sow_reviewer.schemas import ExtractedDocument  # noqa: E402

CASES_DIR = ROOT / "data" / "cases"
OUT_DIR = ROOT / "outputs" / "extracted"
APP_NAME = "sow_reviewer"
USER_ID = "local-dev"


def load_cases(only: str | None) -> list[dict]:
    index = json.loads((CASES_DIR / "index.json").read_text(encoding="utf-8"))["cases"]
    return [c for c in index if only is None or c["id"] == only]


async def extract_one(runner: InMemoryRunner, case: dict) -> tuple[ExtractedDocument, dict]:
    text = (CASES_DIR / case["file"]).read_text(encoding="utf-8")
    session = await runner.session_service.create_session(app_name=APP_NAME, user_id=USER_ID)
    message = types.Content(role="user", parts=[types.Part(text=f"Document for case {case['id']}:\n\n{text}")])

    started = time.perf_counter()
    final_text = None
    usage = {}
    async for event in runner.run_async(user_id=USER_ID, session_id=session.id, new_message=message):
        if event.usage_metadata:
            usage = {
                "prompt_tokens": event.usage_metadata.prompt_token_count,
                "output_tokens": event.usage_metadata.candidates_token_count,
                "total_tokens": event.usage_metadata.total_token_count,
            }
        if event.is_final_response() and event.content and event.content.parts:
            final_text = "".join(p.text or "" for p in event.content.parts)
    elapsed = time.perf_counter() - started

    if final_text is None:
        raise RuntimeError(f"{case['id']}: no final response")
    extracted = ExtractedDocument.model_validate_json(final_text)
    return extracted, {"latency_s": round(elapsed, 2), **usage}


async def main(only: str | None) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    runner = InMemoryRunner(agent=extraction_agent, app_name=APP_NAME)
    rows = []
    for case in load_cases(only):
        extracted, metrics = await extract_one(runner, case)
        out = OUT_DIR / f"{case['id']}.json"
        out.write_text(extracted.model_dump_json(indent=2), encoding="utf-8")
        rows.append((case["id"], extracted.document_type, extracted.principal_amount, metrics))
        print(f"{case['id']}: {extracted.document_type:22s} {extracted.currency} {extracted.principal_amount} "
              f"| {metrics.get('latency_s')}s, {metrics.get('total_tokens')} tokens -> {out.relative_to(ROOT)}")
        if extracted.missing_or_unclear:
            for note in extracted.missing_or_unclear:
                print(f"    flag: {note}")
    (OUT_DIR / "_metrics.json").write_text(
        json.dumps([{"case": r[0], "document_type": r[1], "principal_amount": r[2], **r[3]} for r in rows], indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))
