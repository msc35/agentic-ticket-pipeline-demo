"""The pipeline orchestrator: input -> agent -> validation -> decision.

Wires the fuzzy agent (Part 4) to the deterministic gate (`decide` = schema + grounding +
safety override, Parts 5-6) behind one call. A fresh `Toolbox` per run keeps each request's
evidence trace isolated (all state is in-memory per request — no database).

Two entry points:
  - `run_pipeline(report)`      -> the final `PipelineResult` (blocking).
  - `iter_pipeline(report)`     -> a generator of trace events ending in the result, for the
                                   server to stream live to the browser.
"""

from __future__ import annotations

from typing import Any, Iterator

from app.agent import iter_agent, run_agent
from app.schema import PipelineResult
from app.tools import Toolbox
from app.validation import decide


def run_pipeline(report: str) -> PipelineResult:
    """Run the whole pipeline and return the final decision."""
    toolbox = Toolbox()
    agent_result = run_agent(report, toolbox)
    return decide(agent_result.final_json, toolbox)


def iter_pipeline(report: str) -> Iterator[dict[str, Any]]:
    """Stream the run as events: each agent message / tool call, then the final result.

    Event shapes:
      {"type": "agent_message", "text": ...}
      {"type": "tool_call", "tool": ..., "input": ..., "records": [...]}
      {"type": "result", "result": <PipelineResult as dict>, "parse_ok": bool, "rounds": int}
    """
    toolbox = Toolbox()

    # Drive the agent generator, forwarding each step as it happens.
    gen = iter_agent(report, toolbox)
    agent_result = None
    try:
        while True:
            yield next(gen)
    except StopIteration as stop:
        agent_result = stop.value

    # Agent finished — run the deterministic gate and emit the final result.
    result = decide(agent_result.final_json, toolbox)
    yield {
        "type": "result",
        "result": result.model_dump(),
        "parse_ok": agent_result.parse_ok,
        "rounds": agent_result.rounds,
    }


if __name__ == "__main__":
    import json
    from pathlib import Path

    examples = {e["id"]: e for e in json.load(
        open(Path(__file__).resolve().parent.parent / "data" / "example_inputs.json"))}

    # Blocking form on the clean example.
    print("run_pipeline(EX-1):")
    res = run_pipeline(examples["EX-1"]["report"])
    print(f"  decision={res.decision} grounded={res.grounded} "
          f"safety_override={res.safety_override} asil={res.affected_asil}")
    print(f"  reason: {res.reason}\n")

    # Streaming form on the safety example — show events arriving in order.
    print("iter_pipeline(EX-2) streamed events:")
    for event in iter_pipeline(examples["EX-2"]["report"]):
        if event["type"] == "tool_call":
            ids = [r.get("id") for r in event["records"]] if isinstance(event["records"], list) else []
            print(f"  · tool_call {event['tool']}({event['input']}) -> {ids}")
        elif event["type"] == "agent_message":
            print(f"  · agent_message: {event['text'][:80]}…")
        else:
            r = event["result"]
            print(f"  · result: decision={r['decision']} safety_override={r['safety_override']} "
                  f"asil={r['affected_asil']}")
