"""Sends one case to the deployed agent and prints the event stream.

    export GOOGLE_CLOUD_PROJECT=... GOOGLE_CLOUD_LOCATION=...
    uv run --extra gcp python scripts/smoke_test.py projects/.../reasoningEngines/123
    uv run --extra gcp python scripts/smoke_test.py <resource> sow-010   # pauses for confirmation
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USER_ID = "smoke-test"


async def main(resource: str, case_id: str) -> None:
    import agentplatform

    index = json.loads((ROOT / "data" / "cases" / "index.json").read_text(encoding="utf-8"))["cases"]
    case = next(c for c in index if c["id"] == case_id)
    document = (ROOT / "data" / "cases" / case["file"]).read_text(encoding="utf-8")

    client = agentplatform.Client(
        project=os.environ["GOOGLE_CLOUD_PROJECT"], location=os.environ["GOOGLE_CLOUD_LOCATION"]
    )
    runtime = client.runtimes.get(name=resource)
    session = await runtime.async_create_session(user_id=USER_ID)
    async for event in runtime.async_stream_query(
        message=document, user_id=USER_ID, session_id=session["id"]
    ):
        print(json.dumps(event, ensure_ascii=False, default=str)[:600])


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "sow-004"))
