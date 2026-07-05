"""The Gemini agent loop — the core agentic part.

A hand-rolled tool-calling loop (no framework): the model receives the incident report and
the three tools, investigates by calling them, and finally returns a JSON ticket. We drive
every round ourselves so each decision is visible and defensible:

  - Automatic function calling is DISABLED; we execute each tool call and feed the result
    back explicitly.
  - The number of tool-call rounds is capped, so the loop always terminates.
  - The final answer is parsed as JSON. A parse failure is NOT a crash — it is handed to
    Part 5 as a schema-validation failure (route to human).

Note the division of labour: this module produces the agent's *raw* proposal. It does no
validation, grounding, or safety logic — those are deterministic checks that live in
Part 5/6. The evidence trace is captured by the `Toolbox` passed in, so the pipeline can
read exactly what the tools surfaced.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types

from app.tools import Toolbox

# Load .env from the project root so GEMINI_API_KEY is available without shell exports.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Model + loop bounds. Model is overridable via env; the cap guarantees termination.
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
MAX_TOOL_ROUNDS = 6

# The exact keys the agent must return. Kept here so the prompt and parser agree.
TICKET_KEYS = ["summary", "affected_component_id", "root_cause", "severity",
               "recommended_action", "evidence_ids"]

SYSTEM_PROMPT = f"""\
You are an incident-triage assistant for a chip-development team working under functional
safety (ISO 26262). You investigate a problem report using tools and produce a structured
ticket.

You have three tools:
- search_logs(query, component_id?): search synthetic log events. Returns entries with ids
  like "LOG-001". Optionally filter to one component id.
- lookup_component(component_id): fetch a component record (id like "CMP-001", including
  whether it is safety-relevant and its ASIL level).
- query_known_issues(symptom): search a knowledge base. Returns entries with ids like
  "KI-001", or nothing if no known issue matches.

How to work:
1. INVESTIGATE FIRST. Use the tools before drawing any conclusion. Search the logs for the
   symptoms, identify the component id(s) involved, look up the component, and check the
   known-issues base. Iterate as needed.
2. Determine the single most likely affected component and the most likely root cause,
   grounded in what the tools actually returned.
3. CITE YOUR EVIDENCE. In "evidence_ids", list ONLY the ids that the tools genuinely
   returned and that you actually relied on (log ids "LOG-…", known-issue ids "KI-…",
   and/or the component id "CMP-…"). NEVER invent an id. If an id was not returned by a
   tool, do not cite it.
4. IF YOU CANNOT GROUND IT, DO NOT GUESS. If the tools surface no evidence that supports a
   specific root cause, say so plainly in "root_cause" and return an EMPTY "evidence_ids"
   list. It is correct and expected to hand an uncertain case to a human rather than
   fabricate a cause.
5. Pick "severity" from exactly: "low", "medium", "high", "critical".
6. Also report "confidence": your own estimate, a number from 0.0 to 1.0, of how likely your
   root cause is correct. Be honest. (Note: this is your self-assessment only; the system
   does not use it to decide anything — it independently verifies your evidence — so do not
   inflate it.)

When you are done investigating, respond with ONLY a JSON object — no prose, no markdown
code fences — with exactly these keys:
  {{"summary": str, "affected_component_id": str, "root_cause": str,
    "severity": "low"|"medium"|"high"|"critical", "recommended_action": str,
    "evidence_ids": [str, ...], "confidence": number}}
"""

# When the round cap is reached, we ask the model to stop investigating and finalize.
_FORCE_FINAL = (
    "You have reached the investigation limit. Based on what you have found so far, output "
    "the final JSON ticket now, following the required format exactly."
)

# If the model ends its turn with prose instead of JSON, nudge it once to emit only JSON.
_JSON_CORRECTION = (
    "Your last message was not the required JSON object. Respond now with ONLY the JSON "
    "object described (no prose, no code fences). If you could not ground a root cause, "
    "still return the JSON with an empty \"evidence_ids\" list and a low \"confidence\"."
)
MAX_JSON_RETRIES = 2


# --- tool declarations (JSON schema the model sees) ------------------------------------

def _tool() -> types.Tool:
    """Declare the three tools to Gemini as function declarations."""
    return types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name="search_logs",
            description="Search synthetic log events for a symptom. Returns matching log "
                        "entries (each with an id like 'LOG-001').",
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keywords describing the symptom."},
                    "component_id": {"type": "string", "description": "Optional component id (e.g. 'CMP-002') to filter to."},
                },
                "required": ["query"],
            },
        ),
        types.FunctionDeclaration(
            name="lookup_component",
            description="Fetch a component record by id, including whether it is "
                        "safety-relevant and its ASIL level.",
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "component_id": {"type": "string", "description": "Component id, e.g. 'CMP-002'."},
                },
                "required": ["component_id"],
            },
        ),
        types.FunctionDeclaration(
            name="query_known_issues",
            description="Search the known-issues knowledge base by symptom. Returns "
                        "matching entries (ids like 'KI-001'), or nothing if none match.",
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "symptom": {"type": "string", "description": "Short description of the symptom."},
                },
                "required": ["symptom"],
            },
        ),
    ])


@lru_cache(maxsize=1)
def _client() -> genai.Client:
    """Create the Gemini client from GEMINI_API_KEY (cached). Fails clearly if unset."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    return genai.Client(api_key=api_key)


def _dispatch(toolbox: Toolbox, name: str, args: dict[str, Any]) -> Any:
    """Execute one tool call against the toolbox (which also records the evidence trace)."""
    if name == "search_logs":
        return toolbox.search_logs(args.get("query", ""), args.get("component_id"))
    if name == "lookup_component":
        comp = toolbox.lookup_component(args.get("component_id", ""))
        return [comp] if comp else []
    if name == "query_known_issues":
        return toolbox.query_known_issues(args.get("symptom", ""))
    return {"error": f"unknown tool: {name}"}


def _text_of(resp: types.GenerateContentResponse) -> str:
    """Concatenate the text parts of a response (avoids warnings on tool-only responses)."""
    out: list[str] = []
    if resp.candidates and resp.candidates[0].content and resp.candidates[0].content.parts:
        for part in resp.candidates[0].content.parts:
            if getattr(part, "text", None):
                out.append(part.text)
    return "\n".join(out).strip()


def _extract_json(text: str) -> dict[str, Any] | None:
    """Best-effort parse of the model's final JSON, tolerating stray prose or code fences."""
    if not text:
        return None
    # Strip a ```json ... ``` fence if present.
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[1]
        if cleaned.lstrip().lower().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
    # Fall back to the outermost braces so leading/trailing prose does not break parsing.
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        data = json.loads(cleaned[start:end + 1])
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


@dataclass
class AgentResult:
    """The agent's raw proposal plus an ordered trace of what it did (for the UI)."""

    final_json: dict[str, Any] | None   # parsed final ticket, or None if unparseable
    final_text: str                     # the raw final text the model returned
    parse_ok: bool                      # whether final_text parsed into a JSON object
    rounds: int                         # how many tool-call rounds were used
    steps: list[dict[str, Any]] = field(default_factory=list)  # ordered events for the UI


def iter_agent(report: str, toolbox: Toolbox, max_rounds: int = MAX_TOOL_ROUNDS):
    """Generator form of the loop: yields each step (agent message / tool call) as it happens
    and *returns* the final `AgentResult` (available via `StopIteration.value`).

    Streaming at step granularity gives the UI a live trace — tool calls appear one by one —
    without needing token streaming through the tool loop. `run_agent` below drains this for
    callers that just want the final result.
    """
    client = _client()
    # Investigation config: tools available; hand-rolled loop, so auto function calling OFF.
    tool_config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=[_tool()],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        temperature=0,  # deterministic-as-possible for a reproducible demo
    )
    # Finalization config: NO tools, so the model must emit its answer as text (it cannot
    # keep calling tools to avoid committing). Used to force a clean final JSON.
    final_config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT, temperature=0
    )

    contents: list[types.Content] = [
        types.Content(role="user", parts=[types.Part.from_text(text=report)])
    ]
    steps: list[dict[str, Any]] = []

    def emit(step: dict[str, Any]) -> dict[str, Any]:
        """Record a step in the ordered trace and hand it back to be yielded."""
        steps.append(step)
        return step

    # --- investigation phase: let the model call tools until it stops or hits the cap ---
    final_text = ""
    final_round = max_rounds
    answered = False  # did the model volunteer a final (non-JSON) answer before the cap?

    for round_i in range(max_rounds):
        resp = client.models.generate_content(model=MODEL, contents=contents, config=tool_config)

        text = _text_of(resp)
        if text:
            yield emit({"type": "agent_message", "text": text})

        calls = resp.function_calls or []
        if not calls:
            # The model intends this as its final answer.
            final_text, final_round = text, round_i
            if _extract_json(text) is not None:
                return _finalize(text, steps, round_i)  # clean JSON → done
            contents.append(resp.candidates[0].content)  # keep the prose turn, then correct it
            answered = True
            break

        # Keep the model's own turn (its function_call parts) in the history…
        contents.append(resp.candidates[0].content)
        # …then execute each call and feed the results back in a single user turn.
        fr_parts: list[types.Part] = []
        for call in calls:
            args = dict(call.args or {})
            records = _dispatch(toolbox, call.name, args)
            yield emit({"type": "tool_call", "tool": call.name, "input": args, "records": records})
            fr_parts.append(types.Part.from_function_response(
                name=call.name, response={"result": records}
            ))
        contents.append(types.Content(role="user", parts=fr_parts))

    # --- finalization phase: tools OFF, push for a clean final JSON with a few retries -----
    contents.append(types.Content(
        role="user",
        parts=[types.Part.from_text(text=_JSON_CORRECTION if answered else _FORCE_FINAL)],
    ))
    for _ in range(MAX_JSON_RETRIES + 1):
        resp = client.models.generate_content(model=MODEL, contents=contents, config=final_config)
        text = _text_of(resp)
        if text:
            yield emit({"type": "agent_message", "text": text})
        final_text = text or final_text
        if _extract_json(text) is not None:
            return _finalize(text, steps, final_round)
        contents.append(resp.candidates[0].content)
        contents.append(types.Content(role="user", parts=[types.Part.from_text(text=_JSON_CORRECTION)]))

    return _finalize(final_text, steps, final_round)


def run_agent(report: str, toolbox: Toolbox, max_rounds: int = MAX_TOOL_ROUNDS) -> AgentResult:
    """Run the tool-calling loop to completion and return the agent's raw proposal.

    Thin drainer over `iter_agent` for callers that don't need the live step stream.
    """
    gen = iter_agent(report, toolbox, max_rounds)
    try:
        while True:
            next(gen)
    except StopIteration as stop:
        return stop.value


def _finalize(text: str, steps: list[dict[str, Any]], rounds: int) -> AgentResult:
    data = _extract_json(text)
    return AgentResult(
        final_json=data, final_text=text, parse_ok=data is not None, rounds=rounds, steps=steps
    )


if __name__ == "__main__":
    # Live smoke test on the clean example (EX-1). Makes a real API call.
    examples = json.load(open(Path(__file__).resolve().parent.parent / "data" / "example_inputs.json"))
    ex = next(e for e in examples if e["id"] == "EX-1")
    print(f"Running agent on {ex['id']}: {ex['label']}\n{'-'*70}")

    tb = Toolbox()
    result = run_agent(ex["report"], tb)

    print(f"Tool-call rounds used: {result.rounds}\n")
    print("What the agent did:")
    for step in result.steps:
        if step["type"] == "tool_call":
            n = len(step["records"]) if isinstance(step["records"], list) else 1
            ids = [r.get("id") for r in step["records"]] if isinstance(step["records"], list) else []
            print(f"  · TOOL {step['tool']}({step['input']}) -> {n} record(s) {ids}")
        else:
            print(f"  · AGENT: {step['text'][:120]}")

    print(f"\nParsed final ticket (parse_ok={result.parse_ok}):")
    print(json.dumps(result.final_json, indent=2))
    print(f"\nEvidence trace captured {len(tb.evidence_trace)} tool calls; "
          f"surfaced ids: {sorted(tb.surfaced_ids())}")
