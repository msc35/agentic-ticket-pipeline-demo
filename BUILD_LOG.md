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
