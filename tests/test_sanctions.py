"""sanctions_lookup behaviour (docs/verify_rules.md) and the synthetic list.

The lookup tests fail with NotImplementedError until tools/sanctions_server.py is
written. In mcp 2.3.0 `@server.tool()` returns the original function (checked in
test_decorator_returns_a_plain_callable), so the function is imported and called
directly, with no MCP round trip.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from tools.sanctions_server import sanctions_lookup

ROOT = Path(__file__).resolve().parent.parent
LIST_PATH = ROOT / "data" / "sanctions_synthetic.json"
ENTRIES = json.loads(LIST_PATH.read_text(encoding="utf-8"))
INDEX = json.loads((ROOT / "data" / "cases" / "index.json").read_text(encoding="utf-8"))["cases"]


def only_match(query: str) -> dict:
    result = sanctions_lookup(query)
    assert len(result["matches"]) == 1, result
    return result["matches"][0]


# --- the data file: these pass today ---


def test_decorator_returns_a_plain_callable():
    assert inspect.isfunction(sanctions_lookup)
    assert list(inspect.signature(sanctions_lookup).parameters) == ["name"]


def test_list_has_ten_entries_with_spec_fields():
    assert len(ENTRIES) == 10
    for entry in ENTRIES:
        assert set(entry) == {"name", "aliases", "list_name", "dob"}
        assert isinstance(entry["aliases"], list)
        assert "fictional" in entry["list_name"]


def test_list_covers_alias_and_null_dob():
    assert any(entry["aliases"] for entry in ENTRIES)
    assert any(entry["dob"] is None for entry in ENTRIES)


# --- lookup behaviour ---


def test_exact_name_scores_one():
    match = only_match("Zephyr Quillon Marrowfen")
    assert match["name"] == "Zephyr Quillon Marrowfen"
    assert match["list_name"] == "SYNTHETIC-LIST-A (fictional test data)"
    assert match["score"] == 1.0


def test_match_ignores_case():
    assert only_match("zEPHYR qUILLON mARROWFEN")["score"] == 1.0


def test_match_ignores_punctuation():
    assert only_match("Zephyr, Quillon. Marrowfen")["score"] == 1.0


def test_alias_scores_point_nine_and_returns_primary_name():
    match = only_match("Orrin Talvek Drossmere")
    assert match["name"] == "Cassius Velmont Drossmere"
    assert match["score"] == 0.9


def test_alias_with_punctuation_matches_after_stripping():
    assert only_match("F A Lumbrel")["score"] == 0.9


def test_reordered_name_scores_point_seven():
    match = only_match("Marrowfen Zephyr Quillon")
    assert match["name"] == "Zephyr Quillon Marrowfen"
    assert match["score"] == 0.7


def test_entity_names_match():
    assert only_match("Sablewick Trading Consortium")["score"] == 1.0


def test_no_match_returns_empty_list():
    assert sanctions_lookup("Nobody Inparticular")["matches"] == []


def test_one_shared_word_is_not_a_match():
    # "Drossmere" alone must not hit; not every word of the name is present.
    assert sanctions_lookup("Drossmere")["matches"] == []


def test_word_inside_a_longer_word_is_not_a_match():
    assert sanctions_lookup("Zephyrine Quillon Marrowfenn")["matches"] == []


def test_response_shape():
    result = sanctions_lookup("Zephyr Quillon Marrowfen")
    assert result["query"] == "Zephyr Quillon Marrowfen"
    assert result["checked_at"]
    assert set(result["matches"][0]) == {"name", "list_name", "score"}


@pytest.mark.parametrize("subject", [c["subject"] for c in INDEX if c["id"] != "sow-010"])
def test_case_subjects_do_not_hit(subject):
    assert sanctions_lookup(subject)["matches"] == []


def test_planted_case_hits():
    subject = next(c["subject"] for c in INDEX if c["id"] == "sow-010")
    assert sanctions_lookup(subject)["matches"]
