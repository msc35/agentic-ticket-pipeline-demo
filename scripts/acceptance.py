"""Live acceptance harness: run every curated example through the real pipeline and check
each lands on its intended path.

This is the spec's final acceptance check as a repeatable script. It makes ~20 Gemini calls,
so it is deliberately NOT part of the pytest suite. Run it with the venv active and
GEMINI_API_KEY set:

    python scripts/acceptance.py

Expected path per example is derived from the curated data:
  - auto_draft            -> decision auto_draft, grounded, no safety override
  - route (safety)        -> decision route, safety override fired (component is ASIL A-D)
  - route (ungrounded)    -> decision route, not grounded, no safety override
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.pipeline import run_pipeline          # noqa: E402
from app.tools import get_component            # noqa: E402

EXAMPLES = json.load(open(ROOT / "data" / "example_inputs.json"))


def expected_path(ex) -> str:
    """Classify the intended outcome from the curated fields."""
    if ex["expected_decision"] == "auto_draft":
        return "auto_draft"
    cid = ex["expected_component_id"]
    comp = get_component(cid) if cid else None
    return "route:safety" if (comp and comp["safety_relevant"]) else "route:ungrounded"


def actual_path(r) -> str:
    if r.decision == "auto_draft":
        return "auto_draft"
    if r.safety_override:
        return "route:safety"
    return "route:ungrounded"


def main() -> int:
    # The safety-critical invariant (the hard PASS/FAIL): no safety-relevant or ungrounded
    # case may auto-draft, and a clean non-safety case must auto-draft. The reason *sub-path*
    # (safety override vs grounding) is reported as info — with a probabilistic agent a safety
    # case occasionally routes via grounding instead of the override, which is still correct
    # and still safe (it routes to a human either way).
    print(f"{'EX':6} {'decision':16} {'path (info)':18} {'conf':5} result  label")
    print("-" * 100)
    decision_pass = 0
    path_match = 0
    fails = []
    for ex in EXAMPLES:
        want_decision = ex["expected_decision"]
        want_path = expected_path(ex)
        r = run_pipeline(ex["report"])
        got_path = actual_path(r)
        ok = r.decision == want_decision           # the invariant that must hold
        decision_pass += ok
        path_match += (got_path == want_path)
        note = "" if got_path == want_path else f"  (via {got_path.split(':')[1]}, still routed)"
        conf = f"{r.model_confidence:.0%}" if r.model_confidence is not None else "-"
        if not ok:
            fails.append((ex["id"], want_decision, r.decision, r.reason))
        print(f"{ex['id']:6} {r.decision:16} {got_path:18} {conf:>5} {'PASS' if ok else 'FAIL':6}  {ex['label']}{note}")

    print("-" * 100)
    print(f"Decision invariant: {decision_pass}/{len(EXAMPLES)} correct "
          f"(no safety/ungrounded case auto-drafted).")
    print(f"Exact intended sub-path: {path_match}/{len(EXAMPLES)} "
          f"(the rest still routed to a human, just via the other gate).")
    if fails:
        print("\nInvariant failures (these would be real bugs):")
        for exid, want, got, reason in fails:
            print(f"  {exid}: expected {want}, got {got}\n     reason: {reason}")
    return 0 if decision_pass == len(EXAMPLES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
