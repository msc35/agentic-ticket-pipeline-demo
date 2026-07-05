"""Validation-layer tests: schema gate, grounding check, safety override, and confidence.

All deterministic (no API): we populate an evidence trace by calling the real tools, then
feed crafted raw tickets through the gate — exactly the failure modes that matter.
"""

import pytest

from app.tools import Toolbox
from app.validation import _extract_confidence, decide, evaluate


def _box_for(component_id: str, log_query: str, ki_query: str) -> Toolbox:
    """A toolbox whose trace has surfaced the logs, known issue, and component for a case."""
    tb = Toolbox()
    tb.search_logs(log_query, component_id)
    tb.query_known_issues(ki_query)
    tb.lookup_component(component_id)
    return tb


# A grounded PMU (CMP-002, ASIL D) ticket and its supporting trace.
PMU = dict(
    summary="PMU brownout on VDD_CORE",
    affected_component_id="CMP-002",
    root_cause="Insufficient load-transient margin lets VDD_CORE droop below the brownout threshold.",
    severity="high",
    recommended_action="Retune the PMU load-transient response.",
    evidence_ids=["LOG-004", "KI-002"],
    confidence=0.95,
)

# A grounded QM ticket (CMP-003 logging) and trace.
LOG = dict(
    summary="Log buffer overflow",
    affected_component_id="CMP-003",
    root_cause="The diagnostic ring buffer is undersized and drops entries under burst load.",
    severity="medium",
    recommended_action="Enlarge the ring buffer.",
    evidence_ids=["LOG-002", "KI-001"],
    confidence=0.8,
)


@pytest.fixture
def pmu_box():
    return _box_for("CMP-002", "brownout undervoltage VDD_CORE", "PMU brownout VDD_CORE")


@pytest.fixture
def log_box():
    return _box_for("CMP-003", "log buffer overflow dropped entries", "log buffer overflow dropped entries")


# --- schema gate -----------------------------------------------------------------------

def test_non_json_output_routes_as_schema_failure(pmu_box):
    r = evaluate(None, pmu_box)
    assert r.decision == "route_to_human" and r.schema_valid is False


def test_unknown_component_routes_as_schema_failure(pmu_box):
    r = evaluate({**PMU, "affected_component_id": "CMP-999"}, pmu_box)
    assert r.decision == "route_to_human" and r.schema_valid is False


# --- grounding gate --------------------------------------------------------------------

def test_grounded_qm_ticket_auto_drafts(log_box):
    r = decide(LOG, log_box)
    assert r.decision == "auto_draft" and r.grounded is True and r.safety_override is False


def test_fabricated_evidence_routes(log_box):
    r = evaluate({**LOG, "evidence_ids": ["LOG-999"]}, log_box)
    assert r.decision == "route_to_human" and r.grounded is False
    assert "fabricated" in r.reason.lower()


def test_symptomatic_logs_only_routes(log_box):
    # Cites real logs but no known issue → symptoms without a validated root cause.
    r = evaluate({**LOG, "evidence_ids": ["LOG-001", "LOG-002"]}, log_box)
    assert r.decision == "route_to_human" and r.grounded is False


def test_known_issue_for_wrong_component_routes(log_box):
    # Cites KI-001 (CMP-003) but blames CMP-006 → no known issue for the blamed component.
    r = evaluate({**LOG, "affected_component_id": "CMP-006", "evidence_ids": ["KI-001"]}, log_box)
    assert r.decision == "route_to_human" and r.grounded is False


def test_empty_evidence_routes_as_grounding_failure(log_box):
    r = evaluate({**LOG, "evidence_ids": []}, log_box)
    assert r.decision == "route_to_human" and r.schema_valid is True and r.grounded is False


def test_right_citation_wrong_story_routes(log_box):
    # Cites the correct KI-001 but writes an unrelated root cause → must not ground.
    r = evaluate({**LOG, "root_cause": "The CAN controller entered bus-off after transmit errors.",
                  "evidence_ids": ["KI-001"]}, log_box)
    assert r.decision == "route_to_human" and r.grounded is False
    assert "does not match" in r.reason.lower()


# --- safety override -------------------------------------------------------------------

def test_safety_override_routes_a_grounded_asil_ticket(pmu_box):
    r = decide(PMU, pmu_box)
    assert r.decision == "route_to_human"
    assert r.grounded is True                 # it WAS grounded…
    assert r.safety_override is True and r.affected_asil == "D"   # …but safety forces a route


def test_override_only_intercepts_would_be_auto_drafts(log_box):
    # An ungrounded ticket on a safety component keeps the GROUNDING reason, not a safety one.
    box = _box_for("CMP-002", "brownout undervoltage", "PMU brownout VDD_CORE")
    r = evaluate({**PMU, "evidence_ids": ["LOG-004"]}, box)  # logs only → not grounded
    r2 = decide({**PMU, "evidence_ids": ["LOG-004"]}, box)
    assert r.grounded is False
    assert r2.decision == "route_to_human" and r2.safety_override is False  # stays a quality route


# --- confidence is captured but never decides ------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ({"confidence": 0.9}, 0.9),
    ({"confidence": 92}, 0.92),     # percentage form normalised
    ({"confidence": "high"}, None), # non-numeric dropped
    ({}, None),
    (None, None),
])
def test_confidence_extraction(raw, expected):
    assert _extract_confidence(raw) == expected


def test_confidence_does_not_affect_decision(log_box):
    # Same grounded ticket, wildly different self-reported confidence → identical decision.
    high = decide({**LOG, "confidence": 0.99}, log_box)
    low = decide({**LOG, "confidence": 0.01}, _box_for("CMP-003", "log buffer overflow", "log buffer overflow dropped entries"))
    assert high.decision == low.decision == "auto_draft"
