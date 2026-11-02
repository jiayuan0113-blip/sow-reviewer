"""Creates a RAG Engine corpus in Serverless mode and uploads data/policies/. Costs money.

    GOOGLE_CLOUD_PROJECT=... GOOGLE_CLOUD_LOCATION=us-central1 \\
      uv run --extra gcp python scripts/setup_rag.py

Serverless mode is a preview available only in us-central1. The project default is Spanner
mode, which bills for a dedicated instance even when idle, so main() unprovisions that first.
Unprovisioning deletes any Spanner-mode corpora in the project.

Put the printed RAG_CORPUS line in sow_reviewer/.env. To add files to an existing
corpus, pass its resource name: scripts/setup_rag.py --corpus projects/.../ragCorpora/123
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POLICY_DIR = ROOT / "data" / "policies"
SKIP = {"SOURCES.md"}  # A to-fetch list, not policy content.


def policy_files() -> list[Path]:
    return sorted(p for p in POLICY_DIR.iterdir() if p.is_file() and p.name not in SKIP)


def use_serverless(client, types, project: str, location: str) -> None:
    name = f"projects/{project}/locations/{location}/ragEngineConfig"
    for db_config in (
        types.RagManagedDbConfig(
            spanner=types.RagManagedDbConfigSpanner(unprovisioned=types.RagManagedDbConfigUnprovisioned())
        ),
        types.RagManagedDbConfig(serverless=types.RagManagedDbConfigServerless()),
    ):
        client.rag.update_config(updated_config=types.RagEngineConfig(name=name, rag_managed_db_config=db_config))
    print(client.rag.get_config().rag_managed_db_config)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", help="Existing corpus resource name; omit to create one.")
    parser.add_argument("--display-name", default="sow-reviewer-policies")
    args = parser.parse_args()

    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    location = os.environ["GOOGLE_CLOUD_LOCATION"]

    import agentplatform
    from agentplatform import types

    client = agentplatform.Client(project=project, location=location)
    corpus_name = args.corpus
    if not corpus_name:
        use_serverless(client, types, project, location)
        corpus_name = client.rag.create_corpus(rag_corpus=types.RagCorpus(display_name=args.display_name)).name
        print(f"created corpus {corpus_name}")

    for path in policy_files():
        client.rag.upload_file(corpus_name=corpus_name, path=str(path), display_name=path.name)
        print(f"uploaded {path.name}")

    print(f"\nRAG_CORPUS={corpus_name}")


if __name__ == "__main__":
    main()
