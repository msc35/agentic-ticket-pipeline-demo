# Build Log

A part-by-part account of the agentic incident-to-ticket pipeline. Each entry records
what was written, what it does, why it matters (tied to the interview framing), and how
to check it.

## Part 0 — Project setup

**What I wrote:** Created the folder structure (`app/`, `data/`, `frontend/`) with empty
stubs for the `app/` modules (`schema.py`, `tools.py`, `agent.py`, `validation.py`,
`pipeline.py`, `server.py`, plus `__init__.py`). Added `requirements.txt`
(`google-genai`, `pydantic`, `fastapi`, `uvicorn[standard]`, `python-dotenv`),
`.env.example` (`GEMINI_API_KEY=your_key_here`), `.gitignore` (ignores `.env` and Python
cruft), `README.md`, and this `BUILD_LOG.md`.

**What it does:** Establishes the project skeleton so every later part has a home. No
logic yet — this is scaffolding. Uses the current unified Google Gemini SDK
(`google-genai`), not the deprecated `google-generativeai`.

**Why it matters:** A clean, predictable structure makes the pipeline easy to explain
part by part, which is the whole point of this build. The API key is read from the
environment (never hard-coded), which is the baseline for anything credential-touching.

**How to check it:** `ls -R` shows the structure; `pip install -r requirements.txt`
resolves the dependencies. The `app/` module stubs import cleanly (they're empty).

## Part 1 — Synthetic data (chip + ISO flavored)

**What I wrote:** Four JSON files in `data/`:
- `components.json` — 20 chip components/modules, each with `id`, `name`, `description`,
  `safety_relevant`, and `asil` (`QM` or `A`–`D`). Broad mix of safety-relevant (CTS,
  PMU, Memory Controller, Watchdog, PLL, CAN, Thermal, LBIST, Cache Coherency, Sensor
  Fusion — ASIL B/C/D) and QM (logging, STA tool, verification harness, power model,
  build/config, OTA, trace tool, GPIO, TRNG, JTAG).
- `logs.json` — 42 synthetic events with `id`, `timestamp`, `component_id`, `severity`,
  `message`, in a chip/EDA/tool-qualification voice. Most clusters point cleanly at one
  component; one cluster (CMP-005/006/009) is deliberately noisy/ambiguous.
- `known_issues.json` — 17-entry KB (`pattern`, `root_cause`, `recommended_action`,
  `related_component_id`). Covers every grounded example but **deliberately has no entry
  for CMP-005/006/009 (the noisy cluster) or CMP-020 (JTAG)**, so the ungrounded examples
  genuinely cannot be grounded.
- `example_inputs.json` — **20 curated reports**, each with a `label`, an `intended_path`
  note, a natural-language `report`, plus structured `expected_component_id` and
  `expected_decision` fields used for automated data-support verification (the UI can
  ignore them).

**Distribution of the 20 examples (verified by script):**
- **6 auto-draft** (grounded on QM components): EX-1 logging, EX-5 power model, EX-7 OTA
  rollback, EX-8 GPIO storm, EX-12 TRNG entropy (security ≠ safety), EX-15 trace-tool bug.
- **11 safety override** (grounded, safety-relevant, spanning ASIL B/C/D): EX-2 PMU (D),
  EX-4 STA tool-qual (B, ISO), EX-6 ECC (C), EX-9 PLL (B), EX-10 CAN (C), EX-11 thermal
  (B), EX-13 LBIST coverage gap (C, ISO), EX-14 missing verification link (D, ISO), EX-18
  sensor fusion (D), EX-19 cache coherency (D), EX-20 low-severity single-bit ECC (C).
- **3 ungrounded** (no matching evidence): EX-3 noisy multi-tool, EX-16 contradictory
  theories, EX-17 novel JTAG debug-port dropouts (no logs/KI exist for it at all).

**Deliberate teaching contrasts built into the set:**
- EX-12 (TRNG) is security-relevant but QM → auto-drafts, showing safety ≠ security.
- EX-14 (safety requirement) vs EX-15 (the trace *tool* itself): same tooling area, but
  one concerns a safety-relevant component and overrides, the other doesn't.
- EX-20 is low-severity and "within spec" yet still routes, proving the safety override
  keys on ASIL relevance, not severity.
- EX-17 is a genuinely novel symptom the tools have nothing on, so it routes rather than
  getting a fabricated answer.

**What it does:** Provides the fake world the agent investigates and a rich, path-balanced
set of live demo inputs. No real systems or data — everything synthetic.

**Why it matters:** The demo's power comes from many inputs taking different paths under
the same deterministic gate. The ungrounded cases prove the grounding gate works; the ASIL
cases prove the safety override fires even on well-formed, low-severity tickets.

**How to check it:** A validation script confirms all four files parse, every log/KI
references a real component, `safety_relevant == (asil != QM)` for all 20 components, and
**each example's intended path is actually supported by the data** — grounded examples
have both logs and a KI for their component; ungrounded examples have no KI for their
(optional) named component. All checks pass (auto_draft=6, safety=11, ungrounded=3).

## Part 2 — Mock tools

**What I wrote:** `app/tools.py` — a `Toolbox` class exposing the three investigation
tools over the synthetic data, plus the evidence trace:
- `search_logs(query, component_id=None)` — matching log entries (optional component
  filter).
- `lookup_component(component_id)` — the component record incl. `safety_relevant` / `asil`
  (what the Part 6 override keys on), or `None`.
- `query_known_issues(symptom)` — matching known issues; may return `[]`.
Every call is appended to `self.evidence_trace` as `{tool, input, records}`, and
`surfaced_ids()` returns the set of all record ids the tools actually returned this run.
A fresh `Toolbox` is created per report so each run has an isolated trace.

**What it does:** Gives the agent its only window into the world and, crucially, records
exactly what evidence each call surfaced. Matching is transparent keyword/whole-word
overlap (no vector search): you can read a call and predict its result.

**Why it matters:** The evidence trace is the ground truth the Part 5 grounding check
runs against — it lets us verify the agent cited evidence the tools genuinely returned,
instead of trusting the model. `surfaced_ids()` is the exact set the grounding check
tests citations against.

**Design decisions worth defending:**
- Whole-word (token-set) matching, not raw substring — avoids "intermittent" matching
  inside "intermittently".
- A generous stopword list so function words ("can", "last", "off", "between") never
  drive a match.
- Asymmetric relevance threshold: **logs match on one shared keyword** (short, specific
  messages; maximizes investigation recall; a log match alone can't create false
  grounding), while **known issues require two shared keywords** (long prose; this is the
  match that drives grounding, so it must not fire on incidental single-word overlap).
  This precision is what keeps the ungrounded examples (EX-3/16/17) ungrounded.

**How to check it:** `python -m app.tools` runs a self-demo (one call of each tool + the
surfaced ids). A verification script confirms, for all 20 examples: every grounded
example's expected component has both supporting logs and a matching known issue (using
both the full report and short agent-style queries), and every ungrounded example
surfaces no known issue — even when queried with its entire report text. All pass.

## Part 3 — The Pydantic ticket schema (the deterministic contract)

**What I wrote:** `app/schema.py` with three models:
- `Ticket` — the strict output contract: `summary`, `affected_component_id`, `root_cause`,
  `severity` (Literal low/medium/high/critical), `recommended_action`, and
  `evidence_ids: list[str]`. Validators enforce: `affected_component_id` must be a real
  component id (checked against `tools.all_component_ids()`), all text fields non-blank
  (whitespace-stripped, `min_length=1`), and `evidence_ids` non-empty (blanks/dupes are
  cleaned; a list of only blanks is rejected).
- `EvidenceCall` — one `{tool, input, records}` entry, so the trace is typed.
- `PipelineResult` — the full run result: `decision` (auto_draft/route_to_human),
  plain-English `reason`, the three gate flags (`schema_valid`, `grounded`,
  `safety_override`), `affected_asil` (for the safety badge), the validated `ticket` (or
  `None`), `raw_ticket` (the agent's partial output for the human queue), and
  `evidence_trace`.

**What it does:** Defines the exact, typed shape the agent's output must take. Constructing
a `Ticket` *is* the validation step — if the raw output can't build a valid `Ticket`,
Pydantic raises and Part 5 converts that into a route-to-human decision.

**Why it matters:** This is the core "how do you use a fuzzy agent where output must be
flawless" answer: a probabilistic model, a deterministic gate. It fails **closed** — bad
component id, blank field, wrong severity, or no evidence all block the ticket rather than
letting it through. `evidence_ids` exists specifically so grounding is checkable: the agent
must name the records it relied on, and Part 5 verifies them against the evidence trace.

**One deliberate choice to defend:** extra fields are *ignored*, not rejected. If the model
volunteers a `confidence` score, we drop it rather than fail on it — because we don't trust
self-reported confidence anyway (that's the whole point of the grounding check). Rejecting
extras would also route otherwise-good tickets for a cosmetic reason.

**How to check it:** `python -m app.schema` runs a self-demo: a valid ticket builds (and
shows evidence_ids de-duplicated/cleaned), and unknown component id / empty evidence_ids /
bad severity / blank summary are each correctly rejected; a routed `PipelineResult` with no
ticket builds fine.

## Part 4 — The Gemini agent loop (the core agent)

**What I wrote:** `app/agent.py` — a hand-rolled Gemini tool-calling loop using the current
`google-genai` SDK (v2.10.0), no framework. Pieces:
- The three tools declared as `types.FunctionDeclaration`s (JSON-schema params) wrapped in a
  `types.Tool`. Automatic function calling is **disabled** so the loop is fully hand-driven.
- A system prompt instructing the agent to investigate with tools first, ground its root
  cause, cite ONLY ids the tools actually returned, and — if it cannot ground — say so and
  return an empty `evidence_ids` rather than guess.
- `run_agent(report, toolbox)`: sends the report, and each round executes any
  `response.function_calls` against the `Toolbox` (which records the evidence trace), feeds
  results back as a `role="user"` function-response turn, and repeats. Rounds are capped
  (`MAX_TOOL_ROUNDS=6`) with a final "finalize now" nudge, so it always terminates.
- The final text is parsed to JSON (`_extract_json`, tolerant of code fences / stray prose);
  a parse failure is returned as `parse_ok=False`, NOT a crash — Part 5 treats that as a
  schema failure. Returns an `AgentResult` (parsed json, raw text, rounds, ordered `steps`
  for the UI).

**What it does:** Turns a messy report into a raw ticket proposal by actually investigating
the synthetic world. Model is `gemini-2.5-flash` (overridable via `GEMINI_MODEL`),
`temperature=0` for reproducibility. The client reads `GEMINI_API_KEY` from `.env`/env and
is created lazily so importing the module never requires a key.

**Why it matters:** This is the "agentic pipeline" itself — a transparent, capped,
hand-rolled loop with structured final output. Every step is inspectable, which is the point
versus a no-code canvas. Crucially it does ONLY the fuzzy work (gather → reason → propose);
all deterministic checks live in Part 5/6.

**Live run (EX-1, clean case):** 2 rounds. Round 1: `search_logs` (found LOG-001/002/003
plus two irrelevant matches it ignored), `lookup_component('CMP-003')`, `query_known_issues`
→ KI-001. Round 2: emitted the ticket. Parsed OK; `affected_component_id=CMP-003`,
`severity=medium`, `evidence_ids=[LOG-001, LOG-002, LOG-003, CMP-003, KI-001]` — all cited
ids are in the surfaced set, so it is genuinely grounded.

**How to check it:** `python -m app.agent` runs the loop live on EX-1 and prints the tool
calls, the parsed ticket, and the captured evidence trace. (Requires `GEMINI_API_KEY`.)

## Part 5 — Validation, grounding check, and the decision gate (the star)

**What I wrote:** `app/validation.py` — the deterministic reliability layer, in three steps:
1. **Schema gate** — `validate_ticket(raw)` tries to build the `Ticket` model. Missing
   fields, blank strings, unknown component id, bad severity, empty `evidence_ids`, or
   non-JSON output all fail → route_to_human with a plain-English reason.
2. **Grounding check** — `check_grounding(ticket, toolbox)` verifies the root cause against
   the evidence trace: (a) no fabricated citations (every cited id was genuinely surfaced by
   a tool this run), and (b) at least one cited **known issue** is for the blamed component.
3. **Decision gate** — `evaluate(raw, toolbox)` combines them into a `PipelineResult`
   (schema failed OR not grounded → route_to_human; else auto_draft, still subject to the
   Part 6 safety override). Also added `Toolbox.surfaced_records()` so a cited id resolves
   back to its record.

**Why it matters:** This is the answer to "how do you use a fuzzy agent where output must be
flawless." The agent proposes; deterministic code disposes. We never read the model's
self-reported confidence (unreliable); we verify cited evidence against what the tools
actually returned. Grounding is a fact we can check; confidence is a feeling we cannot.

**The key design decision (worth defending):** logs are *symptoms*, known issues are
*validated root causes*. A ticket is grounded only if it cites a known issue for the blamed
component — symptomatic logs alone are not enough to auto-draft. This surfaced from a real
failure: on EX-3 the agent confidently blamed a component from noisy logs and cited those
real logs; a "cited evidence links to the component" rule would have wrongly auto-drafted
it. Requiring a validated known issue is the conservative, safety-appropriate rule: we
auto-resolve only root causes the knowledge base already recognises; novel or merely
symptomatic reports go to a human, however confident the model sounds.

**How to check it:** `python -m app.validation` runs (A) seven offline gate tests with no API
— grounded ticket auto-drafts; fabricated evidence, a known issue for the wrong component,
symptomatic-logs-only, cites-only-component, empty evidence, unknown component, and non-JSON
all route with the right reason — then (B) live end-to-end: EX-1 (clean) auto_drafts
(matches KI-001), EX-3 (ambiguous) routes as not grounded.

## Part 6 — The ISO 26262 safety override

**What I wrote:** `apply_safety_override(result)` in `app/validation.py`, plus a `decide()`
helper that composes the full gate (`apply_safety_override(evaluate(...))`) as the single
entry point for the pipeline. The override looks up `ticket.affected_component_id` (via the
non-recording `get_component`, so it never pollutes the evidence trace); if that component
is safety-relevant (ASIL A-D), it forces `decision=route_to_human`, sets
`safety_override=True` and `affected_asil`, and writes a distinct safety reason.

**What it does:** Adds the deterministic functional-safety rule that sits above the quality
gates. A safety-relevant component is never auto-drafted — even a perfectly grounded,
high-quality ticket is routed to a human. It keys on the component's ASIL, not on ticket
quality or model confidence.

**Why it matters:** This is the cleanest ISO 26262 story in the build. Under functional
safety, output on a safety-critical path cannot be auto-trusted however good it looks; a
deterministic rule enforces that on top of the probabilistic agent. It ties the whole build
to the interviewer's homework.

**One precedence decision worth defending:** the override only intercepts tickets that would
OTHERWISE AUTO-DRAFT (schema-valid AND grounded). A ticket already routed for a schema or
grounding reason keeps *that* reason. This surfaced from a live EX-3 run where the agent
blamed a safety-relevant component from noisy logs: firing the override there would have
masked the real story (not grounded) behind a safety banner, and made EX-3 non-deterministic
across runs. Gating the override on the auto-draft path keeps safety routing and quality
routing distinct (which the UI shows separately) and loses no safety — an ungrounded safety
ticket still routes, just for the quality reason.

**How to check it:** `python -m app.validation` — section B (offline) shows the same grounded
ticket auto-drafting on a QM component (CMP-003) but routing via the override on an ASIL-D
component (CMP-002); section C (live) shows the clean trio: EX-1 auto_draft, EX-2 routed via
SAFETY OVERRIDE (ASIL D) even though grounded, EX-3 routed for not-grounded (no override).

## Part 7 — The pipeline orchestrator + FastAPI server

**What I wrote:**
- Refactored `app/agent.py`: the tool loop is now a generator `iter_agent(report, toolbox)`
  that **yields each step (agent message / tool call) as it happens** and returns the final
  `AgentResult`. `run_agent` is a thin drainer over it. This enables a live trace without
  token-streaming through the tool loop.
- `app/pipeline.py`: `run_pipeline(report)` (blocking → final `PipelineResult`) and
  `iter_pipeline(report)` (generator that forwards agent steps then emits a final `result`
  event). A fresh `Toolbox` per run keeps each request's evidence trace isolated; all state
  is in-memory per request (no database).
- `app/server.py`: FastAPI app with `GET /examples` (curated inputs for the UI buttons),
  `POST /run` (streams the trace as Server-Sent Events; empty input and exceptions are
  streamed as `error` events so the UI degrades gracefully), and `GET /` (serves
  `frontend/index.html`). CORS open for local use.
- A placeholder `frontend/index.html` (the real UI is Part 8).

**What it does:** Turns the whole pipeline into one HTTP call the browser can run and watch.
The SSE stream lets tool calls appear one by one, then a final result event carries the full
`PipelineResult` (decision, reason, flags, ticket, evidence trace).

**Why it matters:** Makes the reliability logic demonstrable live in a browser — the point
of the build. Streaming at step granularity gives the "watch it investigate" effect while
keeping the hand-rolled loop simple.

**Run command:** `uvicorn app.server:app --port 8000` (venv active, `GEMINI_API_KEY` in
`.env`), then open http://localhost:8000.

**How to check it:** `python -m app.pipeline` runs both the blocking and streaming forms.
With the server up: `GET /examples` returns 20 examples; `POST /run` with a report streams
`tool_call` / `agent_message` events then a `result` event. Verified end-to-end on EX-2 —
the SSE stream showed the tool calls, the agent's ticket, and a final result with
`decision=route_to_human`, `safety_override=true`, `asil=D`.

## Part 8 — The demo UI (single HTML/JS page)

**What I wrote:** `frontend/index.html` — one self-contained page (HTML + CSS + vanilla JS,
no libraries). It has:
- A row of **example buttons** loaded from `GET /examples` (each button's tooltip shows the
  intended path, a hint for the presenter) plus a textarea for a custom report.
- A **live trace panel** that consumes the `POST /run` SSE stream and appends each tool call
  (tool name, inputs, and the returned record ids as chips with the record text on hover) and
  each agent message, in order, as they arrive.
- A **result panel** with: a big decision banner (green "✓ AUTO-DRAFTED" / amber
  "⚠ ROUTED TO HUMAN") with the plain-English reason; a distinct red **"🛡 SAFETY OVERRIDE —
  ASIL X"** badge when the override fired; a plain **grounded / not grounded** flag; a
  **decision-path chain** (Schema → Grounded → Safety → decision) so the logic is visible as
  a chain, not just a verdict; and the drafted **ticket** rendered as labeled fields with a
  colored severity pill and evidence-id chips (or the partial raw output if the schema failed).

**One technical note worth defending:** `/run` is POST + SSE, and the browser `EventSource`
API only supports GET. So the page reads the stream with `fetch()` + a `ReadableStream`
reader and parses the `data: {...}\n\n` frames itself.

**What it does:** Makes the whole pipeline runnable and legible in the browser — you watch
the agent investigate, then see exactly why each case auto-drafted or routed.

**Why it matters:** The visible contrast between an auto-drafted case and a routed case —
and, for routed cases, between a *grounding* route and a *safety* route — is what sells the
demo. The UI's job is to make the deterministic reliability logic obvious to someone watching.

**How to check it:** `uvicorn app.server:app --port 8000`, open http://localhost:8000, and
click the example buttons. Verified: the page is served at `/`, `/examples` populates the
buttons, and the `POST /run` result event carries exactly the fields the UI renders
(decision, reason, grounded, safety_override, affected_asil, and the six ticket fields).
End-to-end backend behaviour confirmed for the clean (EX-1, auto-draft), safety (EX-2, ASIL
D override) and ungrounded (EX-3) cases.

## Enhancements (after Part 8)

Post-core improvements that deepen the reliability story and add engineering rigor. The
core Parts 0-8 still stand; these build on them.

### Confidence vs grounding (the thesis, made visible)
- The agent now also emits a self-reported `confidence` (0..1). It is captured onto
  `PipelineResult.model_confidence` **for display only** and is provably never consulted in
  any decision branch (`_extract_confidence` is display-only; a regression test asserts the
  same ticket with 0.99 vs 0.01 confidence yields the identical decision).
- The UI shows an "agent confidence: N%" badge next to the grounded flag, with a note that
  it is not used in the decision. The contrast is vivid: EX-3/EX-16 route at ~90% confidence
  because they are not grounded; safety cases route at 100% because ASIL relevance overrides.

### Stronger grounding
- The grounding check now also verifies the stated `root_cause` shares keywords with the
  cited known issue (`keyword_overlap`), catching a "right citation, wrong story" ticket
  (cites the correct KI id but writes an unrelated cause).
- Refined the schema/grounding split: empty `evidence_ids` is now allowed by the schema
  (well-formed but unsupported) and rejected by the **grounding** gate ("cites no evidence").
  Schema checks shape; grounding checks support. Auto-draft still requires real grounded
  evidence, so nothing weakens.

### Agent robustness
- Split the loop into an investigation phase (tools on) and a finalization phase (tools
  OFF) that forces a clean final JSON with a couple of correction retries. This stops a
  hesitant agent from returning prose (which would misfire as a schema failure) — the noisy
  cases now reliably reach the grounding gate instead.
- Data fix: reworded LOG-042 ("recovered via a protocol reset" instead of "via timeout") so
  the word "timeout" no longer bridges EX-3's noise to the cache-coherency known issue.

### Test suite + acceptance harness
- `tests/` — 51 deterministic pytest tests (no API): schema contract, tool matching + the
  evidence trace, per-example data-support (grounded examples have logs+KI; ungrounded
  surface no KI), and every gate/override/confidence path. Run: `pytest -q`.
- `scripts/acceptance.py` — live harness running all 20 examples through the real pipeline
  and checking each lands on its intended path (auto_draft / route:safety / route:ungrounded).
  Latest run: **20/20**. Run: `python scripts/acceptance.py`.
- `requirements-dev.txt` adds `pytest`.

### UI transparency & polish
- Component ASIL / safety shown on every ticket (not only on override).
- "Evidence considered" section: cited (green) vs surfaced-but-unused (grey) chips, making
  grounding visible.
- Cmd/Ctrl+Enter to run, copy-ticket-as-JSON, and automatic dark mode.

## Part 9 — Docker wrap + temporary public hosting

**What I wrote:**
- `Dockerfile` — `python:3.11-slim`, installs `requirements.txt`, copies `app/ data/
  frontend/`, runs as a non-root user, and starts uvicorn on `$PORT` (default 8000). The
  `GEMINI_API_KEY` is passed at run time, never baked into the image.
- `.dockerignore` — keeps the image slim and never ships `.env`, `.venv`, `.git`, tests, or
  docs.
- Optional access guard in `app/server.py`: if `APP_PASSWORD` is set, an HTTP Basic-auth
  middleware gates every route. Browsers prompt once and then send the credentials on all
  requests (including the fetch/SSE calls), so no frontend change is needed. Unset by
  default (open), which is fine for local use.
- `scripts/share.sh` — one command that runs the server locally and exposes it on a
  temporary public `https://…trycloudflare.com` URL via Cloudflare Tunnel. The URL is live
  only while the script runs; closing it leaves nothing online — which matches the "host
  from my laptop while I need it" requirement.

**What it does:** Makes the app portable (container) and gives a zero-standing-infra way to
show it on another machine (the interview laptop) through a public URL, optionally password
-protected.

**Why it matters:** "Containerized, reproducible, runs anywhere" is a clean, true line, and
the laptop tunnel means the demo is reachable from any browser during the interview without
deploying a persistent server or leaving the API key on a host.

**How to check it:**
- Auth: `APP_PASSWORD=secret123 uvicorn app.server:app --port 8010` then curl — no creds and
  wrong password return 401, correct password returns 200 (verified).
- Tunnel: `./scripts/share.sh` prints a public URL; verified `cloudflared` registers a tunnel
  connection to the Cloudflare edge (the URL then works from any browser after warm-up).
- Docker: `docker build -t agentic-ticket .` then
  `docker run --rm -p 8000:8000 -e GEMINI_API_KEY=... agentic-ticket` (Dockerfile provided;
  Docker isn't installed on this machine, so the image build is documented, not run here).
