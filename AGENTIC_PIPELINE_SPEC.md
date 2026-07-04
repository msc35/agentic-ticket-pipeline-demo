# Agentic Incident-to-Ticket Pipeline — Build Spec

## What we are building (read this first)

A small but real agentic pipeline that mirrors the example the interviewer gave:
an agent takes a messy problem report from a chip-development context, investigates
it using tools, reasons about the root cause, and produces a structured, validated
ticket. The point that makes this interview-worthy is **not** the ticket drafting.
It is the **reliability layer** wrapped around a probabilistic agent so it can be
used in a deterministic, safety-regulated world:

- The agent output must pass a **strict schema** (deterministic gate on fuzzy output).
- The claimed root cause must be **grounded in evidence the tools actually returned**
  (we do NOT trust the model's self-reported confidence).
- Anything touching a **safety-relevant (ASIL-rated) component** is routed to a human
  **regardless** of confidence (an ISO 26262-style deterministic safety override).
- High-confidence, grounded, non-safety tickets are auto-drafted. Everything else is
  routed to a human queue **with the agent's partial work and a plain reason why**.

This is a demonstration tool. It runs in the browser, shows the agent's reasoning
live, and clearly shows the auto-draft vs route-to-human decision for each case.

Everything is **synthetic**. No real Infineon systems, data, or internal tools.

---

## Interview framing (why each design choice exists)

Keep this section in mind while building. Every choice below is something to defend
out loud in the room.

- **Code, not a no-code tool.** A research reviewer wants to see and probe the agent
  logic, not a drag-and-drop canvas. Hand-rolled tool loop, no LangChain, so every
  decision is visible and defensible. (It also matches the RAG project on the CV.)
- **Pydantic schema = the deterministic contract.** Probabilistic model, deterministic
  gate. If the output does not fit the schema, it does not pass. This is the core
  answer to "how do you use agents where output must be flawless."
- **Grounding check, not model confidence.** LLM self-reported confidence is
  unreliable. So we check whether the tools actually surfaced evidence for the claimed
  root cause. Knowing this failure mode and engineering around it is itself the signal.
- **Safety override = ISO 26262 thinking.** Under functional safety, tools on
  safety-critical paths must be trusted/qualified and their output human-validated.
  The override encodes exactly that: safety-relevant components never auto-resolve.
- **Human-in-the-loop by design.** The agent does the heavy lifting (gather, reason,
  draft). A human keeps the binding decision on anything uncertain or safety-relevant.
  Speed of agents, safety of humans.

---

## Tech stack (keep it minimal and defensible)

- **Python** backend.
- **Gemini API** for the agent (native function calling / tool use). Use the current
  official Google Gemini Python SDK. Claude Code: check the current SDK name and
  function-calling syntax before writing the loop, since this changes.
- **Pydantic** for the ticket schema and validation. This is central, not incidental.
- **FastAPI** to expose the pipeline and stream the reasoning trace to the browser.
- **Plain HTML + CSS + vanilla JS** frontend. No framework. One page.
- **Docker** wrap. Optional, only after the core works end to end.

Deliberately excluded: LangChain / LlamaIndex, any database, any auth, any UI
framework. All state is in-memory per request. All data is synthetic files.

The Gemini API key is provided via an environment variable `GEMINI_API_KEY`. Never
hard-code it. Read it from the environment.

---

## Required vs optional

- **Required (the core demo):** Parts 0 through 8. This is the full working,
  demonstrable pipeline. Build these solidly.
- **Optional (only if the core is clean):** Part 9 (Docker) and Part 10 extras.
  Do not start these until Parts 0-8 run end to end.

---

## How Claude Code should work through this

Build **one part at a time, in order**. After finishing each part:

1. Confirm the part runs / imports cleanly on its own where possible.
2. Append a short entry to a file called `BUILD_LOG.md` in the project root, in this
   shape:
   ```
   ## Part N — [name]
   What I wrote: [files created/changed]
   What it does: [plain-English, 2-4 sentences]
   Why it matters: [the design reason, tied to the interview framing above]
   How to check it: [command to run, or what to look at]
   ```
3. Then stop and summarize the same thing in the chat before moving to the next part.

The goal is that the person building this can read `BUILD_LOG.md` afterward and
understand and defend every piece. Clarity over cleverness. Comment the non-obvious
lines, especially in the validation and grounding logic.

---

## Project structure (target)

```
agentic-ticket-pipeline/
├── BUILD_LOG.md
├── README.md
├── requirements.txt
├── .env.example
├── data/
│   ├── logs.json
│   ├── components.json
│   └── known_issues.json
├── app/
│   ├── __init__.py
│   ├── schema.py          # Pydantic models (the deterministic contract)
│   ├── tools.py           # mock tools over synthetic data
│   ├── agent.py           # Gemini tool-calling loop
│   ├── validation.py      # grounding check + decision gate + safety override
│   ├── pipeline.py        # orchestrates: input -> agent -> validation -> decision
│   └── server.py          # FastAPI app, streams the trace
├── frontend/
│   └── index.html         # single-page UI (HTML + CSS + JS in one file)
└── Dockerfile             # optional, Part 9
```

---

# PART 0 — Project setup

**Goal:** scaffold the project so everything after has a home.

**Build:**
- Create the folder structure above (empty stubs for the `app/` modules are fine).
- `requirements.txt` with: the Gemini SDK, `pydantic`, `fastapi`, `uvicorn`,
  `python-dotenv`. Pin nothing exotic.
- `.env.example` containing `GEMINI_API_KEY=your_key_here`.
- A short `README.md` explaining what the project is (one paragraph) and how to run it
  (fill in the run command once the server exists).
- Start `BUILD_LOG.md`.

**Why it matters:** clean structure makes the pipeline easy to explain part by part,
which is the whole point of this build.

**After this part:** write the Part 0 entry in `BUILD_LOG.md` and summarize.

---

# PART 1 — Synthetic data (chip + ISO flavored)

**Goal:** create the fake world the agent investigates. This is where the
chip-development and ISO-standard flavor lives.

**Build three JSON files in `data/`:**

**`components.json`** — a list of chip components/modules. Each has: `id`, `name`,
`description`, and a `safety_relevant` boolean plus an `asil` field (one of `QM`, `A`,
`B`, `C`, `D`, where `QM` means not safety-relevant and `A`-`D` are ASIL safety
levels). Include a mix. Examples of the flavor to invent (keep them plausible but
clearly synthetic):
- a clock tree synthesis module (safety-relevant, ASIL B),
- a power management unit (safety-relevant, ASIL D),
- a general logging utility (QM, not safety-relevant),
- a memory controller, a timing-analysis tool, a verification harness, etc.

**`logs.json`** — a list of synthetic log/event entries. Each has: `id`, `timestamp`,
`component_id` (referencing a component), `severity` (`info`/`warning`/`error`), and
`message`. Write messages in a chip/EDA/tool-qualification voice, for example: timing
violations, verification mismatches, a tool-qualification check failing, a drift in a
model used for power estimation, a checksum/config mismatch. Some log clusters should
clearly point at one component; some should be noisy/ambiguous on purpose.

**`known_issues.json`** — a small knowledge base. Each entry has: `id`, `pattern`
(a short description of a symptom), `root_cause`, `recommended_action`, and
`related_component_id`. Include some ISO-flavored entries, for example: a tool used in
a safety-relevant flow that lost its qualification status, or a verification gap that
would violate an ISO 26262 requirement. Leave some real-world symptoms with NO matching
known issue, so the agent sometimes cannot ground its answer (this is important for the
demo).

**Also create 4-6 curated example inputs** (put them in `data/` as a separate file,
e.g. `example_inputs.json`, each with a short label and a `report` text). Design them
on purpose to hit different paths:
1. A clean case that maps to a known issue on a **non-safety** component → should
   auto-draft.
2. A case on a **safety-relevant (ASIL D)** component that even if confident → must be
   routed to human by the safety override.
3. A noisy/ambiguous case where the agent **cannot ground** the root cause → routed to
   human, low confidence.
4. A case that looks like an ISO tool-qualification lapse → routed to human, safety.
Add one or two more freely.

**Why it matters:** the demo's power comes from showing different inputs taking
different paths. The curated examples are what you run live in the interview. The
ungrounded and safety cases are the ones that prove the gate actually works.

**After this part:** write the Part 1 entry in `BUILD_LOG.md`, note which example maps
to which intended path, and summarize.

---

# PART 2 — Mock tools

**Goal:** give the agent a small set of tools to investigate the synthetic world.

**Build in `app/tools.py` three plain Python functions:**
- `search_logs(query: str, component_id: str | None = None)` → returns matching log
  entries from `logs.json` (simple keyword/substring match is fine).
- `lookup_component(component_id: str)` → returns the component record, including its
  `safety_relevant` / `asil` fields.
- `query_known_issues(symptom: str)` → returns matching entries from
  `known_issues.json` (simple match). May return empty.

Each function loads from the JSON files and returns plain Python dicts/lists. Keep the
matching simple and transparent, no vector search needed here.

**Important:** have each tool call also record what it returned into an
**evidence trace** (a list the pipeline can read later). The grounding check in Part 5
depends on knowing exactly what evidence the tools surfaced. So structure tools so the
pipeline can capture, per call: tool name, input, and the raw records returned.

**Why it matters:** these are the agent's only window into the world. The evidence they
return is later used to check whether the agent's conclusion is actually supported, or
invented.

**After this part:** write the Part 2 entry, show one example call and its output, and
summarize.

---

# PART 3 — The Pydantic ticket schema (the deterministic contract)

**Goal:** define the exact, strict shape the agent's final output MUST take. This is the
deterministic gate.

**Build in `app/schema.py` a Pydantic model, e.g. `Ticket`, with fields like:**
- `summary: str`
- `affected_component_id: str`
- `root_cause: str`
- `severity: Literal["low", "medium", "high", "critical"]`
- `recommended_action: str`
- `evidence_ids: list[str]` — the ids of the specific logs / known-issues / components
  the agent used to justify the root cause. **This field is what makes grounding
  checkable.** The agent must cite which evidence it relied on.

Add validation: `affected_component_id` must be a known component id, `severity` must be
one of the allowed values, `evidence_ids` must not be empty for an auto-draft. Use
Pydantic validators where useful.

Also define a small result model, e.g. `PipelineResult`, holding: the `Ticket` (or
partial ticket), the `decision` (`auto_draft` or `route_to_human`), the `reason` for
the decision, the `evidence_trace`, and a `grounded: bool` flag.

**Why it matters:** this is the single strongest talking point. The agent is fuzzy, but
its output must clear a hard, typed contract before anything downstream trusts it. If it
does not fit the schema, it fails closed. Say exactly that in the interview.

**After this part:** write the Part 3 entry, explain the schema and why `evidence_ids`
exists, and summarize.

---

# PART 4 — The Gemini agent loop (the core agent)

**Goal:** the actual agentic part. Gemini receives the problem report, is given the
three tools, and runs a tool-calling loop until it can produce a ticket.

**Build in `app/agent.py`:**
- Register the three tools from Part 2 as Gemini function-callable tools (use Gemini's
  native function-calling / automatic-function-calling mechanism, whichever the current
  SDK supports). Claude Code: verify the current Gemini function-calling API before
  writing this.
- A system/instruction prompt that tells the agent its job: investigate the report
  using the tools, find the most likely root cause, and produce a ticket that fits the
  schema, **including the `evidence_ids` it actually relied on**. Instruct it to only
  cite evidence ids that the tools genuinely returned, and to say plainly if it cannot
  find grounding.
- The loop: send the report, let the model call tools, feed results back, repeat until
  the model returns a final structured answer. Cap the number of tool-call rounds (e.g.
  a small max) so it always terminates.
- Ask the model to return its final answer as JSON matching the schema fields, then
  parse it into the `Ticket` model. If parsing fails, that is a validation failure to be
  handled in Part 5, not a crash.
- Throughout, record the **evidence trace** (every tool call and what it returned) and
  the agent's reasoning steps, so the UI can show them live.

**Why it matters:** this is the "agentic pipeline" the role is about. Hand-rolled loop,
capped rounds, structured final output. You can explain every step.

**After this part:** write the Part 4 entry, run it once on the clean example input,
and summarize what the agent did (which tools it called, what ticket it produced).

---

# PART 5 — Validation, grounding check, and the decision gate (the star)

**Goal:** wrap the fuzzy agent output in deterministic checks. This is the part that
makes the whole thing round-two-worthy. Build it carefully and comment it well.

**Build in `app/validation.py`:**

**1. Schema validation (deterministic gate).**
Take the agent's raw final output and try to build the `Ticket` Pydantic model. If it
does not validate (missing fields, bad component id, empty `evidence_ids`, bad
severity), the result is `route_to_human` with reason "output failed schema validation."

**2. Grounding check (do NOT trust model confidence).**
For a ticket that passes the schema, verify grounding:
- Every id in `ticket.evidence_ids` must actually appear in the evidence trace (i.e. the
  tools genuinely returned it). If the agent cited evidence that was never surfaced, it
  is **hallucinated grounding** → `grounded = False`.
- At least one piece of cited evidence must plausibly connect to the claimed
  `affected_component_id` / `root_cause` (a simple check is fine: e.g. cited evidence
  references the same component, or a known issue whose root cause overlaps). If nothing
  connects, `grounded = False`.
- If grounded is False → `route_to_human`, reason "root cause not grounded in retrieved
  evidence."

Add a clear comment here explaining WHY we check grounding instead of asking the model
how confident it is: self-reported confidence is unreliable, so we verify against actual
retrieved evidence.

**3. The decision gate.**
Combine everything into the final decision:
- If schema failed OR not grounded → `route_to_human`.
- Otherwise, in principle `auto_draft` — subject to the safety override in Part 6.

Return a fully populated `PipelineResult` (decision, reason, ticket or partial, evidence
trace, grounded flag). The `reason` must be plain English, because the UI shows it.

**Why it matters:** this is your answer to "flawless, deterministic production." The
agent proposes; deterministic code disposes. Grounding-over-confidence is the detail
that shows you know how LLMs fail.

**After this part:** write the Part 5 entry, run it on the clean example (should pass
grounding) and the ambiguous example (should fail grounding and route to human), and
summarize both outcomes.

---

# PART 6 — The ISO 26262 safety override

**Goal:** add the deterministic safety rule that sits above everything else.

**Build (extend `app/validation.py` or the pipeline):**
- After the decision gate, look up `ticket.affected_component_id` via `lookup_component`.
- If that component is `safety_relevant` (ASIL A/B/C/D, i.e. not `QM`), then the decision
  is **forced to `route_to_human`**, with reason like "safety-relevant component (ASIL X)
  requires human review under functional-safety policy" — **even if the ticket was
  perfectly grounded and high quality.**
- Make sure the UI can show clearly that this override fired, and that it fired for a
  safety reason, not a quality reason. These are different and both worth showing.

**Why it matters:** this is the cleanest ISO 26262 story you can tell. Under functional
safety, output on safety-critical paths cannot be auto-trusted, however good it looks.
A deterministic rule enforces that, on top of the probabilistic agent. This single
feature ties your build directly to the interviewer's homework.

**After this part:** write the Part 6 entry, run the safety example (ASIL D) and show
that it routes to human via the override even when grounded, and summarize.

---

# PART 7 — The pipeline orchestrator + FastAPI server

**Goal:** wire input → agent → validation → decision into one call, and expose it so the
browser can run it and watch the trace.

**Build:**
- `app/pipeline.py`: a single function, e.g. `run_pipeline(report: str)`, that runs the
  agent (Part 4), then validation + gate (Part 5), then the safety override (Part 6), and
  returns the `PipelineResult` plus the reasoning/evidence trace.
- `app/server.py`: a FastAPI app with:
  - an endpoint to list the curated example inputs (so the UI can offer them as buttons),
  - an endpoint that takes a report and runs the pipeline. **Stream the trace** so the UI
    can show tool calls and reasoning as they happen (Server-Sent Events or a simple
    streaming response is fine; if streaming is fiddly with the Gemini loop, a first
    version that returns the full trace at the end is acceptable, but prefer streaming
    for the demo effect).
  - serve the `frontend/index.html` page.
- Enable CORS for local use if needed.

**Why it matters:** turns the logic into something you can show live in a browser during
the interview.

**After this part:** write the Part 7 entry, give the exact run command
(`uvicorn ...`), confirm the endpoints respond, and summarize.

---

# PART 8 — The demo UI (single HTML/JS page)

**Goal:** a clean one-page interface that makes the agent's reasoning and the final
decision legible. Function over beauty, but it should look tidy and professional.

**Build `frontend/index.html`** (HTML + CSS + vanilla JS in one file):
- A row of buttons for the curated example inputs (loaded from the server), plus a text
  area to paste a custom report.
- A "Run" button that calls the pipeline endpoint.
- A **live trace panel** that shows, in order: each tool call and a short view of what it
  returned, and the agent's reasoning steps. If streaming, append as they arrive.
- A **result panel** with:
  - the final ticket rendered as labeled fields (summary, component, root cause,
    severity, action, evidence ids),
  - a **big clear decision banner** at the top: green "AUTO-DRAFTED" or amber
    "ROUTED TO HUMAN", with the plain-English `reason` underneath,
  - if the safety override fired, show a distinct badge (e.g. "SAFETY OVERRIDE — ASIL X")
    so it is obvious this was a safety routing, not a quality routing,
  - the `grounded` flag shown plainly (grounded / not grounded).
- Keep the styling minimal and readable: system font, generous spacing, clear color for
  the two decision states. No external CSS/JS libraries.

**Why it matters:** the visible contrast between an auto-drafted case and a
routed-to-human case (whether for grounding or for safety) is what sells the demo. The
UI's job is to make your reliability logic obvious to someone watching.

**After this part:** write the Part 8 entry, confirm the full flow works in the browser
for at least the clean case, the ungrounded case, and the safety case, and summarize.

---

# PART 9 — Docker wrap (OPTIONAL, only if core is clean)

**Goal:** containerize so it "runs anywhere," echoing the RAG project.

**Build:** a `Dockerfile` that installs requirements, copies the app and data, and runs
uvicorn. Document the build/run commands in the README. Pass the `GEMINI_API_KEY` in as
an environment variable at run time, never baked into the image.

**Why it matters:** lets you say "containerized, reproducible, runs anywhere," which is a
clean, true line and consistent with your existing work. Nice to have, not essential.

**After this part:** write the Part 9 entry and summarize.

---

# PART 10 — Extra polish (OPTIONAL)

Only if everything above is solid. Pick freely from:
- Add a couple more curated examples that hit edge cases (e.g. a ticket that passes
  schema but cites evidence from the wrong component).
- Show a tiny per-run "decision path" summary (schema pass → grounded? → safety? →
  decision) so the logic is visible as a chain.
- A short `NOTES_FOR_INTERVIEW.md` where the person can jot how each design choice maps
  to a likely question. (Content is yours, not Claude Code's to invent.)

**After this part:** write the Part 10 entry and summarize.

---

## Final acceptance check (before calling it done)

Run all curated examples through the UI and confirm:
- The clean non-safety case **auto-drafts**.
- The ASIL safety case **routes to human via the safety override**, even though the
  ticket is well-formed.
- The ambiguous case **routes to human for lack of grounding**.
- The ISO tool-qualification case **routes to human** for the right reason.
- Every decision shows a clear, plain-English reason in the UI.
- `BUILD_LOG.md` has an entry for every part built.

If all of that holds, the demo is ready to show and, more importantly, ready to defend.
