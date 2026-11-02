from sow_reviewer.citations import (
    amounts_in,
    clauses_in,
    unsupported_amounts,
    unsupported_clauses,
    unsupported_quotes,
)

DOC = "| 15 Jul 2026 | CASH DEPOSIT BRANCH 014 | | 60,000.00 |\nsum of **Singapore Dollars (S$350,000.00)** from"


def test_quote_matches_across_markdown_and_whitespace():
    assert unsupported_quotes(["cash deposit branch 014 60,000.00", "Singapore  Dollars (S$350,000.00)"], DOC) == []


def test_quote_matches_when_punctuation_follows_bold_text():
    assert unsupported_quotes(["remuneration is SGD 198,000.00, paid"], "remuneration is **SGD 198,000.00**, paid") == []


def test_invented_quote_is_reported():
    assert unsupported_quotes(["CASH DEPOSIT BRANCH 015"], DOC) == ["CASH DEPOSIT BRANCH 015"]


def test_amounts_parse_separators_and_skip_small_numbers():
    assert amounts_in("S$2,750,000.00 and 406970.55 and clause 4.2, 14 days") == {2750000.0, 406970.55}


def test_invented_amount_is_reported():
    assert unsupported_amounts("Deposit of 60,000 and 61,000", [DOC]) == [61000.0]


def test_known_extracted_amount_is_allowed():
    # A computed annual total (sow-004) appears in the extraction, not in the document.
    assert unsupported_amounts("Annual pay 300,100", [DOC], known=[300100.0]) == []


def test_policy_text_can_support_a_threshold():
    assert unsupported_amounts("Threshold 100,000", [DOC, "SOW-POL 4.2: 100,000 or more"]) == []


def test_clause_must_come_from_policy_answer():
    assert clauses_in("see SOW-POL  4.2") == {"SOW-POL 4.2"}
    assert unsupported_clauses("SOW-POL 4.2 and SOW-POL 9.9", "per SOW-POL 4.2") == ["SOW-POL 9.9"]
