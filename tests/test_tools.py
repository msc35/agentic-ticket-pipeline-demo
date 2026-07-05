"""Tool + data tests: matching behaviour, the evidence trace, and — importantly — that every
curated example's intended path is actually supported by the synthetic data (a deterministic
regression guard, no API needed)."""

import json
from pathlib import Path

import pytest

from app.tools import Toolbox, all_component_ids, get_component, keyword_overlap

DATA = Path(__file__).resolve().parent.parent / "data"
EXAMPLES = json.load(open(DATA / "example_inputs.json"))


def _ungrounded(example) -> bool:
    return example["intended_path"].startswith(("Logs are scattered", "Vague report", "A genuinely novel"))


# --- basic tool behaviour --------------------------------------------------------------

def test_search_logs_matches_and_records_trace():
    tb = Toolbox()
    hits = tb.search_logs("brownout undervoltage", "CMP-002")
    ids = {h["id"] for h in hits}
    assert {"LOG-004", "LOG-005"} <= ids
    assert all(h["component_id"] == "CMP-002" for h in hits)  # component filter honoured
    assert tb.evidence_trace[-1]["tool"] == "search_logs"


def test_lookup_component_returns_safety_fields_or_none():
    tb = Toolbox()
    assert tb.lookup_component("CMP-002")["asil"] == "D"
    assert tb.lookup_component("CMP-999") is None


def test_query_known_issues_can_be_empty():
    tb = Toolbox()
    assert tb.query_known_issues("intermittent testbench timeout flakiness") == []
    assert any(k["id"] == "KI-002" for k in tb.query_known_issues("PMU brownout VDD_CORE"))


def test_surfaced_ids_and_records():
    tb = Toolbox()
    tb.search_logs("brownout", "CMP-002")
    tb.query_known_issues("PMU brownout VDD_CORE")
    assert "LOG-004" in tb.surfaced_ids()
    assert tb.surfaced_records()["KI-002"]["related_component_id"] == "CMP-002"


def test_keyword_overlap_ignores_stopwords():
    assert keyword_overlap("the buffer overflowed", "buffer overflow event") >= {"buffer"}
    assert keyword_overlap("the and of", "with by to") == set()


# --- data-support regression: every example's intended path is backed by the data ------

@pytest.mark.parametrize("ex", [e for e in EXAMPLES if not _ungrounded(e)], ids=lambda e: e["id"])
def test_grounded_examples_have_supporting_logs_and_known_issue(ex):
    cid = ex["expected_component_id"]
    tb = Toolbox()
    logs = tb.search_logs(ex["report"])
    kis = tb.query_known_issues(ex["report"])
    assert any(l["component_id"] == cid for l in logs), f"{ex['id']}: no supporting log for {cid}"
    assert any(k["related_component_id"] == cid for k in kis), f"{ex['id']}: no known issue for {cid}"


@pytest.mark.parametrize("ex", [e for e in EXAMPLES if _ungrounded(e)], ids=lambda e: e["id"])
def test_ungrounded_examples_surface_no_known_issue(ex):
    # Even querying with the entire report text, the KB must return nothing — this is what
    # keeps the ungrounded examples genuinely ungroundable.
    tb = Toolbox()
    assert tb.query_known_issues(ex["report"]) == [], f"{ex['id']}: unexpectedly matched a known issue"


def test_component_data_is_consistent():
    for cid in all_component_ids():
        c = get_component(cid)
        assert c["safety_relevant"] == (c["asil"] != "QM")
