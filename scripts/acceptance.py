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
    print(f"{'EX':6} {'expected':16} {'actual':16} {'conf':5} result  label")
    print("-" * 92)
    passed = 0
    failures = []
    for ex in EXAMPLES:
        want = expected_path(ex)
        r = run_pipeline(ex["report"])
        got = actual_path(r)
        ok = want == got
        passed += ok
        conf = f"{r.model_confidence:.0%}" if r.model_confidence is not None else "-"
        mark = "PASS" if ok else "FAIL"
        if not ok:
            failures.append((ex["id"], want, got, r.reason))
        print(f"{ex['id']:6} {want:16} {got:16} {conf:>5} {mark:6}  {ex['label']}")

    print("-" * 92)
    print(f"{passed}/{len(EXAMPLES)} examples on their intended path.")
    if failures:
        print("\nFailures:")
        for exid, want, got, reason in failures:
            print(f"  {exid}: expected {want}, got {got}\n     reason: {reason}")
    return 0 if passed == len(EXAMPLES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
