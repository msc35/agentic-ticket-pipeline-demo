"""The deterministic reliability layer: schema gate + grounding check + decision gate.

This is the part that makes the pipeline usable in a world where output must be trusted.
The agent proposes; this module disposes. Three deterministic steps:

  1. Schema validation — does the raw output fit the strict `Ticket` contract at all?
  2. Grounding check  — is the claimed root cause actually supported by evidence the tools
                        returned, rather than asserted by the model?
  3. Decision gate    — combine the two into auto_draft vs route_to_human.
  4. Safety override  — (ISO 26262) force a route for any safety-relevant component, even
                        when steps 1-3 all passed.

`decide()` composes all four; it is the single entry point the pipeline uses.

WHY WE CHECK GROUNDING INSTEAD OF ASKING THE MODEL HOW CONFIDENT IT IS
----------------------------------------------------------------------
An LLM's self-reported confidence is unreliable: models are routinely, fluently confident
about fabricated claims. So we do not ask the model "how sure are you?" and we do not read
any confidence it volunteers. Instead we verify the *cited evidence* against the actual
evidence trace.

WHAT "GROUNDED" MEANS HERE
-------------------------
We treat logs as *symptoms* and known issues as *validated root causes*. A ticket is
grounded only when (a) every id it cites was genuinely returned by a tool this run (no
fabricated citations), and (b) it cites a **known issue** whose component matches the one
it blamed — i.e. the root cause corresponds to a previously validated issue, corroborated
by real evidence. Symptomatic logs alone, however confidently the model narrates them, are
not sufficient to auto-draft. This is a deliberately conservative rule for a
safety-regulated setting: we only auto-resolve root causes the knowledge base already
recognises; anything novel or merely symptomatic goes to a human.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.schema import PipelineResult, Ticket
from app.tools import Toolbox, get_component


# --- step 1: schema validation ---------------------------------------------------------

def validate_ticket(raw: dict[str, Any] | None) -> tuple[Ticket | None, str | None]:
    """Try to build a valid `Ticket` from the agent's raw output.

    Returns (ticket, None) on success, or (None, plain_english_reason) on failure. A `None`
    input means the agent never produced parseable JSON — also a schema failure.
    """
    if raw is None:
        return None, "the agent did not return valid JSON output"
    try:
        return Ticket.model_validate(raw), None
    except ValidationError as e:
        return None, _summarize_schema_error(e)


def _summarize_schema_error(e: ValidationError) -> str:
    """Turn a Pydantic error into one short, human-readable clause for the UI."""
    parts = []
    for err in e.errors()[:2]:  # first couple is plenty for a reason line
        field = err["loc"][-1] if err["loc"] else "(root)"
        parts.append(f"{field}: {err['msg']}")
    return "; ".join(parts)


# --- step 2: grounding check -----------------------------------------------------------

def _is_known_issue_for(record: dict[str, Any], component_id: str) -> bool:
    """True if `record` is a known issue whose validated root cause is for `component_id`.

    Known issues carry `related_component_id`; logs and component records do not, so this
    also distinguishes a validated-root-cause citation from a mere symptom (log) or a bare
    component citation.
    """
    return record.get("related_component_id") == component_id


def check_grounding(ticket: Ticket, toolbox: Toolbox) -> tuple[bool, str]:
    """Verify the ticket's root cause is grounded in the evidence the tools actually returned.

    Two deterministic conditions, both must hold:
      (a) No hallucinated citations — every id in `evidence_ids` was genuinely surfaced by a
          tool this run.
      (b) A validated root cause — at least one cited **known issue** is for the blamed
          `affected_component_id`. Cited logs are symptoms and a cited component id just
          proves existence; neither, on its own, grounds a *cause*.
    """
    surfaced = toolbox.surfaced_ids()
    records = toolbox.surfaced_records()
    cited = ticket.evidence_ids

    # (a) Hallucinated grounding: the agent cited evidence no tool ever returned.
    fabricated = [c for c in cited if c not in surfaced]
    if fabricated:
        return False, (
            f"root cause not grounded in retrieved evidence: cited evidence "
            f"{', '.join(fabricated)} was never returned by any tool (fabricated citation)"
        )

    # (b) The root cause must correspond to a validated known issue for the blamed
    #     component. Logs (symptoms) alone are not enough to auto-draft a cause.
    supporting_kis = [
        c for c in cited if c in records and _is_known_issue_for(records[c], ticket.affected_component_id)
    ]
    if not supporting_kis:
        return False, (
            f"root cause not grounded in retrieved evidence: no cited known issue "
            f"corroborates a root cause for {ticket.affected_component_id} "
            f"(symptomatic logs alone are not sufficient to auto-draft)"
        )

    return True, (
        f"root cause grounded — matches validated known issue "
        f"{', '.join(supporting_kis)} for {ticket.affected_component_id}"
    )


# --- step 3: decision gate -------------------------------------------------------------

def evaluate(raw_output: dict[str, Any] | None, toolbox: Toolbox) -> PipelineResult:
    """Run schema validation + grounding + the base decision gate.

    Returns a fully-populated `PipelineResult`. This is the decision BEFORE the ISO 26262
    safety override (Part 6): a clean, grounded ticket lands on `auto_draft` here and the
    override may later force it to `route_to_human`.
    """
    trace = toolbox.evidence_trace

    # Step 1 — schema gate. Fail closed if the output doesn't fit the contract.
    ticket, schema_err = validate_ticket(raw_output)
    if ticket is None:
        return PipelineResult(
            decision="route_to_human",
            reason=f"Output failed schema validation ({schema_err}).",
            schema_valid=False,
            grounded=False,
            ticket=None,
            raw_ticket=raw_output,
            evidence_trace=trace,
        )

    # Step 2 — grounding gate. Do not trust the model; verify against the evidence trace.
    grounded, ground_reason = check_grounding(ticket, toolbox)
    if not grounded:
        return PipelineResult(
            decision="route_to_human",
            # Uppercase only the first letter so component ids (CMP-…) keep their casing.
            reason=ground_reason[0].upper() + ground_reason[1:] + ".",
            schema_valid=True,
            grounded=False,
            ticket=ticket,
            raw_ticket=raw_output,
            evidence_trace=trace,
        )

    # Step 3 — passed both gates → auto_draft (still subject to the Part 6 safety override).
    return PipelineResult(
        decision="auto_draft",
        reason=f"Schema valid and {ground_reason}.",
        schema_valid=True,
        grounded=True,
        ticket=ticket,
        raw_ticket=raw_output,
        evidence_trace=trace,
    )


# --- step 4: the ISO 26262 safety override ---------------------------------------------

def apply_safety_override(result: PipelineResult) -> PipelineResult:
    """Force route_to_human for any safety-relevant (ASIL A-D) component.

    This is the deterministic functional-safety rule that sits ABOVE everything else. Under
    ISO 26262, output on a safety-critical path cannot be auto-trusted no matter how good it
    looks — so a safety-relevant component is *never* auto-drafted, even when the ticket is
    perfectly grounded and high quality. The override keys on the component's safety
    relevance, NOT on ticket quality or model confidence.

    It only intercepts tickets that would OTHERWISE AUTO-DRAFT (schema-valid and grounded).
    A ticket already routed for a schema or grounding reason stays routed for *that* reason —
    this keeps safety routing and quality routing distinct, which the UI shows separately.
    (No safety is lost: an ungrounded safety-relevant ticket still routes, just for the
    quality reason.) Uses the non-recording `get_component` so this internal check never
    pollutes the agent's evidence trace.
    """
    if result.decision != "auto_draft":
        return result  # already routed for a schema/grounding reason — leave it, keep it distinct

    component = get_component(result.ticket.affected_component_id)  # non-None on the auto_draft path
    if component and component["safety_relevant"]:
        asil = component["asil"]
        return result.model_copy(update={
            "decision": "route_to_human",
            "safety_override": True,
            "affected_asil": asil,
            "reason": (
                f"Safety override: {component['id']} ({component['name']}) is "
                f"safety-relevant (ASIL {asil}), so it requires human review under ISO 26262 "
                f"functional-safety policy — safety-relevant output is never auto-drafted, "
                f"regardless of confidence or grounding."
            ),
        })
    return result


def decide(raw_output: dict[str, Any] | None, toolbox: Toolbox) -> PipelineResult:
    """Full deterministic decision: schema + grounding gate, then the safety override.

    This is the single entry point the pipeline (Part 7) uses.
    """
    return apply_safety_override(evaluate(raw_output, toolbox))


# --- self-test -------------------------------------------------------------------------

def _banner(result: PipelineResult) -> str:
    override = f" | SAFETY OVERRIDE (ASIL {result.affected_asil})" if result.safety_override else ""
    return (f"decision={result.decision} | schema_valid={result.schema_valid} | "
            f"grounded={result.grounded}{override}\n    reason: {result.reason}")


if __name__ == "__main__":
    # Part A: OFFLINE deterministic tests (no API) — populate a trace, then craft tickets
    # that exercise each failure mode of the gate.
    print("=== A. Offline gate tests (no API) ===\n")
    tb = Toolbox()
    tb.search_logs("brownout undervoltage", "CMP-002")   # surfaces LOG-004, LOG-005
    tb.query_known_issues("PMU brownout on VDD_CORE")    # surfaces KI-002
    tb.lookup_component("CMP-002")                        # surfaces CMP-002
    print(f"(trace surfaced: {sorted(tb.surfaced_ids())})\n")

    good = dict(summary="PMU brownout on VDD_CORE", affected_component_id="CMP-002",
                root_cause="Insufficient load-transient margin causes VDD_CORE brownout.",
                severity="high", recommended_action="Retune PMU response.",
                evidence_ids=["LOG-004", "KI-002"])

    cases = {
        "grounded ticket": good,
        "hallucinated evidence (LOG-999 never surfaced)": {**good, "evidence_ids": ["LOG-999"]},
        "disconnected evidence (cites a known issue for the wrong component)":
            {**good, "affected_component_id": "CMP-004", "evidence_ids": ["LOG-004", "KI-002"]},
        "symptomatic logs only, no known issue (the EX-3 shape)":
            {**good, "evidence_ids": ["LOG-004", "LOG-005"]},
        "cites only the component id (no real evidence)":
            {**good, "evidence_ids": ["CMP-002"]},
        "schema fail: empty evidence_ids": {**good, "evidence_ids": []},
        "schema fail: unknown component": {**good, "affected_component_id": "CMP-999"},
        "schema fail: not JSON (agent returned nothing)": None,
    }
    for label, raw in cases.items():
        print(f"[{label}]")
        print("   ", _banner(evaluate(raw, tb)).replace("\n", "\n   "), "\n")

    # Part B: OFFLINE safety-override tests — the same grounded ticket, one QM component and
    # one ASIL-D component, showing the override flips only the safety-relevant one.
    print("=== B. Offline safety-override tests (no API) ===\n")

    # A grounded ASIL-D ticket (PMU / CMP-002): auto_draft BEFORE the override, routed after.
    base_d = evaluate(good, tb)
    print("[grounded ASIL-D ticket (CMP-002)]")
    print("    before override:", _banner(base_d).replace("\n", "\n    "))
    print("    after  override:", _banner(apply_safety_override(base_d)).replace("\n", "\n    "), "\n")

    # A grounded QM ticket (logging / CMP-003): unaffected by the override.
    qm_box = Toolbox()
    qm_box.search_logs("log buffer overflow dropped entries", "CMP-003")
    qm_box.query_known_issues("log buffer overflow dropped entries")
    qm_ticket = dict(summary="Log buffer overflow", affected_component_id="CMP-003",
                     root_cause="Ring buffer undersized.", severity="medium",
                     recommended_action="Enlarge buffer.", evidence_ids=["LOG-002", "KI-001"])
    print("[grounded QM ticket (CMP-003)]")
    print("    after override:", _banner(decide(qm_ticket, qm_box)).replace("\n", "\n    "), "\n")

    # Part C: LIVE end-to-end trio — auto-draft, safety override, ungrounded.
    import json
    from pathlib import Path
    from app.agent import run_agent

    print("=== C. Live end-to-end via decide() (agent -> full gate) ===\n")
    examples = {e["id"]: e for e in json.load(
        open(Path(__file__).resolve().parent.parent / "data" / "example_inputs.json"))}
    for exid in ("EX-1", "EX-2", "EX-3"):
        ex = examples[exid]
        box = Toolbox()
        agent_result = run_agent(ex["report"], box)
        result = decide(agent_result.final_json, box)
        print(f"{exid} — {ex['label']}")
        print("   ", _banner(result).replace("\n", "\n   "), "\n")
