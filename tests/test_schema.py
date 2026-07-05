"""Schema contract tests: the Ticket model is the deterministic gate, so pin its behaviour."""

import pytest
from pydantic import ValidationError

from app.schema import Ticket

BASE = dict(
    summary="Log buffer overflow",
    affected_component_id="CMP-003",
    root_cause="Ring buffer undersized.",
    severity="medium",
    recommended_action="Enlarge the buffer.",
    evidence_ids=["LOG-002", "KI-001"],
)


def test_valid_ticket_builds():
    t = Ticket(**BASE)
    assert t.affected_component_id == "CMP-003"
    assert t.evidence_ids == ["LOG-002", "KI-001"]


def test_unknown_component_rejected():
    with pytest.raises(ValidationError):
        Ticket(**{**BASE, "affected_component_id": "CMP-999"})


def test_bad_severity_rejected():
    with pytest.raises(ValidationError):
        Ticket(**{**BASE, "severity": "urgent"})


@pytest.mark.parametrize("field", ["summary", "root_cause", "recommended_action"])
def test_blank_text_fields_rejected(field):
    with pytest.raises(ValidationError):
        Ticket(**{**BASE, field: "   "})


def test_evidence_ids_deduped_and_blanks_dropped():
    t = Ticket(**{**BASE, "evidence_ids": ["LOG-002", "LOG-002", "  ", "KI-001"]})
    assert t.evidence_ids == ["LOG-002", "KI-001"]


def test_empty_evidence_ids_allowed_by_schema():
    # Empty evidence is a well-formed but unsupported ticket — grounding rejects it, not schema.
    t = Ticket(**{**BASE, "evidence_ids": []})
    assert t.evidence_ids == []


def test_extra_fields_ignored_not_rejected():
    # A volunteered `confidence` must not break validation — we ignore it, not trust it.
    t = Ticket(**{**BASE, "confidence": 0.99})
    assert not hasattr(t, "confidence")
