"""Deletes the deployed agent and the RAG corpus so nothing keeps billing.

    uv run --extra gcp python scripts/teardown.py <reasoningEngine resource> <ragCorpus resource>

Both are recreated by scripts/deploy.sh and scripts/setup_rag.py.
"""

from __future__ import annotations

import sys


def main(engine: str, corpus: str) -> None:
    import agentplatform

    project, location = engine.split("/")[1], engine.split("/")[3]
    client = agentplatform.Client(project=project, location=location)
    client.runtimes.delete(name=engine, force=True)
    print(f"deleted {engine}")
    client.rag.delete_corpus(name=corpus)
    print(f"deleted {corpus}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])
