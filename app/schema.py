"""The deterministic contract: strict Pydantic models for the agent's output.

This is the single strongest idea in the build. The agent is probabilistic, but its
output must clear a hard, typed contract before anything downstream trusts it. If the
output does not fit the schema, it does not pass — it fails *closed* and is routed to a
human. The `evidence_ids` field is what makes grounding checkable (Part 5): the agent must
cite the specific records it relied on, and we later verify those citations against what
the tools actually returned.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.tools import all_component_ids

# The two closed vocabularies used below. Keeping them as Literals means Pydantic itself
# rejects anything off-list — no hand-written membership checks needed.
Severity = Literal["low", "medium", "high", "critical"]
Decision = Literal["auto_draft", "route_to_human"]


class Ticket(BaseModel):
    """The structured ticket the agent must produce. Every field is required and typed.

    Construction *is* validation: if the raw agent output cannot build a valid `Ticket`
    (missing field, blank string, unknown component id, bad severity, or no evidence),
    Pydantic raises and Part 5 turns that into a route-to-human decision.
    """

    # str_strip_whitespace makes "   " collapse to "" so blank-but-present fields still
    # fail the min_length=1 checks below (fail closed, not "technically present").
    model_config = ConfigDict(str_strip_whitespace=True)

    summary: str = Field(min_length=1, description="One-line description of the problem.")
    affected_component_id: str = Field(min_length=1, description="Must be a known component id.")
    root_cause: str = Field(min_length=1, description="The agent's diagnosed root cause.")
    severity: Severity = Field(description="One of: low, medium, high, critical.")
    recommended_action: str = Field(min_length=1, description="What should be done.")
    # May be empty at the SCHEMA level: an evidence-less ticket is well-formed but simply
    # unsupported. Whether the cited evidence actually grounds the root cause (and the "no
    # evidence at all" case) is judged by the grounding gate, not here — schema checks shape,
    # grounding checks support. An empty list can therefore never reach auto_draft.
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="Ids of the specific logs / known-issues / components the agent relied "
        "on. This is what makes the root cause grounding-checkable.",
    )

    @field_validator("affected_component_id")
    @classmethod
    def _must_be_known_component(cls, v: str) -> str:
        """Reject a hallucinated component id. The contract only accepts real components."""
        if v not in all_component_ids():
            raise ValueError(f"unknown component id: {v!r}")
        return v

    @field_validator("evidence_ids")
    @classmethod
    def _clean_evidence_ids(cls, v: list[str]) -> list[str]:
        """Drop blanks/dupes while preserving order. May legitimately end up empty (an
        unsupported ticket) — that is handled by the grounding gate, not the schema."""
        cleaned: list[str] = []
        for raw in v:
            item = raw.strip()
            if item and item not in cleaned:
                cleaned.append(item)
        return cleaned


class EvidenceCall(BaseModel):
    """One recorded tool call in the evidence trace: what was asked and what came back.

    Mirrors the dicts produced by `Toolbox` (Part 2); Pydantic coerces them on the way in.
    """

    tool: str
    input: dict[str, Any]
    records: list[dict[str, Any]]


class ComponentInfo(BaseModel):
    """Minimal component facts surfaced to the UI (so it can show ASIL on every ticket)."""

    id: str
    name: str
    safety_relevant: bool
    asil: str


class PipelineResult(BaseModel):
    """The full result of one pipeline run — everything the UI needs to render a decision.

    Holds the validated ticket (or None if it failed the schema), the raw agent output for
    display of partial work, the decision and a plain-English reason, and the flags that
    let the UI show *why* a case routed (schema, grounding, or the safety override).
    """

    decision: Decision
    reason: str  # plain English — the UI shows this verbatim

    # The three gates, surfaced individually so the UI can show the decision path
    # (schema pass -> grounded? -> safety override? -> decision) rather than just a verdict.
    schema_valid: bool
    grounded: bool
    safety_override: bool = False

    # ASIL of the affected component when the safety override fires — powers the
    # "SAFETY OVERRIDE — ASIL X" badge in the UI.
    affected_asil: Optional[str] = None

    # Facts about the blamed component (when the ticket is schema-valid), so the UI can show
    # ASIL / safety relevance on every ticket, not only when the override fires.
    affected_component: Optional[ComponentInfo] = None

    # The agent's OWN self-reported confidence (0..1), captured for display only. It is
    # deliberately NOT used in any decision — the whole point is that we verify grounding
    # instead of trusting this number. Surfacing it makes that contrast visible.
    model_confidence: Optional[float] = None

    # The validated ticket if it passed the schema, else None. `raw_ticket` keeps whatever
    # the agent produced (even if invalid) so the human queue can see the partial work.
    ticket: Optional[Ticket] = None
    raw_ticket: Optional[dict[str, Any]] = None

    evidence_trace: list[EvidenceCall] = Field(default_factory=list)


if __name__ == "__main__":
    from pydantic import ValidationError

    print("1) A valid ticket builds fine:")
    t = Ticket(
        summary="Diagnostic logs dropped under burst load",
        affected_component_id="CMP-003",
        root_cause="Logging ring buffer undersized; writer backpressure drops entries.",
        severity="medium",
        recommended_action="Enlarge the ring buffer and enable overflow-to-disk.",
        evidence_ids=["LOG-002", "KI-001", "LOG-002", "  "],  # dup + blank get cleaned
    )
    print(f"   OK -> evidence_ids cleaned to {t.evidence_ids}\n")

    # Note: empty evidence_ids is intentionally NOT a schema failure anymore — it is a
    # well-formed but unsupported ticket, caught by the grounding gate (Part 5).
    print("\n   (empty evidence_ids now builds — support is judged by grounding, not schema:",
          f"{Ticket(**{**dict(summary='x', affected_component_id='CMP-003', root_cause='x', severity='low', recommended_action='x'), 'evidence_ids': []}).evidence_ids})\n")

    for label, kwargs in {
        "unknown component id": dict(affected_component_id="CMP-999"),
        "bad severity": dict(severity="urgent"),
        "blank summary": dict(summary="   "),
    }.items():
        base = dict(
            summary="x", affected_component_id="CMP-003", root_cause="x",
            severity="low", recommended_action="x", evidence_ids=["LOG-001"],
        )
        base.update(kwargs)
        try:
            Ticket(**base)
            print(f"2) {label}: UNEXPECTEDLY VALID")
        except ValidationError as e:
            print(f"2) {label}: correctly rejected ({e.error_count()} error)")

    print("\n3) A PipelineResult with no valid ticket (routed):")
    r = PipelineResult(
        decision="route_to_human",
        reason="output failed schema validation",
        schema_valid=False,
        grounded=False,
        raw_ticket={"summary": "…", "affected_component_id": "CMP-999"},
    )
    print(f"   {r.decision} — {r.reason} (schema_valid={r.schema_valid})")
