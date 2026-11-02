"""Synthetic sanctions-list lookup exposed as an MCP tool (stdio).

Spec: docs/verify_rules.md (second half). The list is fictional test data.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.mcpserver import MCPServer

LIST_PATH = Path(__file__).resolve().parent.parent / "data" / "sanctions_synthetic.json"
ENTRIES = json.loads(LIST_PATH.read_text(encoding="utf-8"))

EXACT_NAME_SCORE = 1.0
ALIAS_SCORE = 0.9
ALL_WORDS_SCORE = 0.7

server = MCPServer("sanctions-lookup")


def _words(name: str) -> list[str]:
    """Lowercase, punctuation out: "F. A. Lumbrel" becomes ["f", "a", "lumbrel"]."""
    return re.sub(r"[^\w\s]", " ", name).casefold().split()


def _score(query: list[str], entry: dict) -> float:
    """The best score this entry earns, 0.0 if it does not match."""
    aliases = [_words(a) for a in entry["aliases"]]
    primary = _words(entry["name"])
    if query == primary:
        return EXACT_NAME_SCORE
    if query in aliases:
        return ALIAS_SCORE
    # Every word of a listed name is present, in any order. Whole words only, so
    # "Drossmere" alone and "Zephyrine" do not hit.
    if any(set(listed) <= set(query) for listed in [primary, *aliases]):
        return ALL_WORDS_SCORE
    return 0.0


@server.tool()
def sanctions_lookup(name: str) -> dict:
    """Look up a name against the synthetic sanctions list."""
    query = _words(name)
    matches = []
    for entry in ENTRIES:
        score = _score(query, entry)
        if score:
            matches.append({"name": entry["name"], "list_name": entry["list_name"], "score": score})
    matches.sort(key=lambda m: m["score"], reverse=True)
    return {"query": name, "matches": matches, "checked_at": datetime.now(timezone.utc).isoformat()}


if __name__ == "__main__":
    server.run(transport="stdio")
