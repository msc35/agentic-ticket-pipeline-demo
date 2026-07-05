# Interview notes

Study notes for defending this project. Everything here is factual about what the code
does — **read it, then make the framing your own.** The strongest move in the room is to
speak from genuine understanding, not a script.

---

## 30-second pitch

> "It's an agentic pipeline for a functional-safety context. An LLM agent investigates a
> messy incident report with tools and drafts a structured ticket — but the interesting part
> isn't the drafting, it's the **deterministic reliability layer** wrapped around a
> probabilistic model. The output has to pass a strict schema, the root cause has to be
> **grounded in evidence the tools actually returned** (I don't trust the model's
> confidence), and anything touching a **safety-relevant (ASIL-rated) component** is routed
> to a human no matter what — an ISO 26262-style override. Good, grounded, non-safety
> tickets auto-draft; everything else goes to a human with the partial work and a plain
> reason why."

---

## Demo script (what to click, what to say)

Run three examples in this order — they map to the three outcomes:

1. **EX-1 (Log buffer overflow).** → green **AUTO-DRAFTED**.
   "Non-safety component, the agent cited a known issue that matches its root cause, so it's
   grounded and auto-drafts. Watch the trace on the left — it searched logs, looked up the
   component, checked known issues."
2. **EX-2 (PMU brownout, ASIL D).** → amber **ROUTED**, red **SAFETY OVERRIDE** badge.
   "Same quality of work — it's grounded, the agent is 100% confident — but it's a
   safety-relevant component, so the ISO 26262 override forces it to a human regardless.
   Note the badge says *safety*, not *quality*."
3. **EX-3 (Noisy flakiness).** → amber **ROUTED**, *not grounded*, confidence ~90%.
   "This is the money slide. The agent is 90% confident, but the noise doesn't match any
   validated known issue, so grounding fails and it routes. I verify grounding; I never
   trust the confidence number — you can see it sitting right there being ignored."

Optional fourth: **EX-21 (telltale, ASIL A)** to show even the lowest safety level routes,
or **EX-12 (TRNG)** to show security-relevant ≠ safety-relevant (it auto-drafts).

---

## The four pillars (likely questions + grounded answers)

### 1. "How do you use an LLM where the output has to be flawless?"
- **Answer:** a probabilistic model behind a **deterministic gate**. The agent's output must
  build a strict Pydantic `Ticket` — typed fields, `severity` from a fixed set,
  `affected_component_id` must be a real component. If it doesn't fit, it **fails closed** and
  routes to a human. Construction *is* validation.
- **Nuance to volunteer:** extra fields (like a volunteered `confidence`) are *ignored*, not
  rejected — failing a good ticket over a cosmetic field would be wrong. And empty evidence is
  *allowed by the schema* but caught by grounding — schema checks **shape**, grounding checks
  **support**.

### 2. "You said you don't trust the model's confidence — why, and what do you do instead?"
- **Answer:** self-reported confidence is unreliable; models are fluently confident about
  fabricated claims. So I don't ask "how sure are you?" — I **verify the cited evidence**
  against what the tools actually returned. Every cited id must have been surfaced by a tool
  (no hallucinated citations), and the root cause must correspond to a **validated known
  issue** for the blamed component, with the stated cause actually matching that issue's text.
- **Why "known issue," not just logs:** logs are *symptoms*; known issues are *validated root
  causes*. I only auto-resolve causes the knowledge base already recognises — a conservative,
  safety-appropriate stance. Novel or merely symptomatic reports go to a human.
- **Killer demo line:** EX-3 sits at 90% confidence and still routes. The confidence number is
  on screen, being ignored.

### 3. "Where does ISO 26262 come in?"
- **Answer:** the **safety override**. After the quality gates, if the blamed component is
  safety-relevant (ASIL A–D, i.e. not QM), the decision is forced to route-to-human — even a
  perfectly grounded, high-quality ticket. Under functional safety, output on a safety-critical
  path can't be auto-trusted however good it looks; a deterministic rule enforces that on top
  of the probabilistic agent.
- **Nuance to volunteer:** the override only intercepts tickets that would *otherwise
  auto-draft*, so **safety routing stays distinct from quality routing** — the UI shows which
  fired. And it keys on *safety relevance, not severity or ASIL height*: EX-20 is low-severity
  and EX-21 is ASIL A, and both still route.

### 4. "Why keep a human in the loop at all — isn't the point to automate?"
- **Answer:** speed of agents, safety of humans. The agent does the heavy lifting — gather,
  reason, draft — and a human keeps the *binding* decision on anything uncertain or
  safety-relevant. Every routed case arrives with the agent's partial work and a plain-English
  reason, so the human starts from a draft, not a blank page.

---

## How it works (architecture, ~30 seconds)

```
report ─▶ agent (Gemini tool loop) ─▶ raw JSON ─▶ [schema gate] ─▶ [grounding gate] ─▶ [safety override] ─▶ decision
                │                                     │                  │                    │
         search_logs / lookup_component /       Pydantic Ticket    verify cited evidence   ASIL check
         query_known_issues  (evidence trace)                      vs the evidence trace
```

- **Agent (`app/agent.py`):** hand-rolled Gemini function-calling loop — no LangChain, so every
  step is visible and defensible. Capped rounds (always terminates). Finalization runs with
  **tools off** so a hesitant model must emit JSON instead of stalling.
- **Tools (`app/tools.py`):** three keyword-search tools over synthetic JSON; every call is
  recorded into an **evidence trace** — the ground truth the grounding check runs against.
- **Schema (`app/schema.py`):** the `Ticket` contract + `PipelineResult`.
- **Validation (`app/validation.py`):** schema → grounding → safety override; `decide()`
  composes them.
- **Server (`app/server.py`):** FastAPI; `/run` streams the trace to the browser via SSE.

---

## Anticipated harder questions (honest answers)

- **"Why not LangChain / a no-code agent builder?"** A research reviewer wants to see and probe
  the logic, not a drag-and-drop canvas. The hand-rolled loop makes every decision inspectable
  and keeps the dependency surface tiny. (Also mirrors the RAG work on my CV.) *(← make this
  your own.)*
- **"Why Gemini?"** It was the required stack for this exercise; the design is model-agnostic —
  the reliability layer doesn't care which LLM proposes the ticket.
- **"How do you *know* the grounding check works?"** 52 deterministic unit tests (no API) cover
  every gate — fabricated evidence, wrong-component citation, symptoms-only, right-citation-
  wrong-story, empty evidence, the safety override precedence — plus a live acceptance harness
  that runs all 21 curated examples and checks each lands on its intended path.
- **"What if the knowledge base is incomplete?"** Then a real issue with no matching known issue
  is *ungrounded* and routes to a human — by design. I'd rather a false "route" (a human looks
  at something fine) than a false "auto-draft" (a wrong ticket auto-resolves). In safety, that
  asymmetry is the correct bias.
- **"So it over-routes?"** Yes, deliberately. The failure mode I optimise *against* is a
  confident wrong auto-draft. Routing something that was actually fine is cheap; a human just
  confirms it. I'd tune the grounding strictness with real data.
- **"Grounding is keyword overlap — isn't that brittle?"** It's intentionally simple and
  transparent for a demo you can reason about line by line. In production I'd swap the matcher
  for embeddings/semantic similarity behind the *same interface* — the architecture (verify
  citations against a retrieved evidence trace) doesn't change.
- **"How would this scale / go to production?"** Real tools instead of JSON files, a vector
  index for retrieval, a proper human-review queue, per-request tracing/metrics, auth, and
  tool *qualification* evidence for anything on a safety path (ISO 26262 tool confidence level).

---

## Known limitations & what I'd do next (say this unprompted — it reads as maturity)

- Grounding is keyword-based; embeddings would catch paraphrase better.
- The agent can still pick the wrong (but valid) component from noisy input — grounding usually
  catches it, but a second-opinion / consistency check would harden it.
- No retries/backoff on API errors yet; no auth on the pipeline beyond the optional demo
  password.
- Everything is synthetic and in-memory per request — deliberate for the demo; production needs
  real data sources and a persistence/queue layer.

---

## Quick facts

- **Stack:** Python, `google-genai` (Gemini), Pydantic, FastAPI + SSE, vanilla HTML/JS. No
  agent framework, no database, no external JS/CSS libraries.
- **Everything synthetic** — no real systems or data.
- **Run:** `uvicorn app.server:app --port 8000`. **Test:** `pytest -q`.
  **Acceptance:** `python scripts/acceptance.py`. **Share:** `./scripts/share.sh`.
- **The one sentence to land:** *"The agent proposes; deterministic code disposes — and on a
  safety path, a human always disposes."*
