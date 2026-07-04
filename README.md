# Agentic Incident-to-Ticket Pipeline

An agentic pipeline for a chip-development / functional-safety context. A Gemini agent
takes a messy incident report, investigates it with tools over synthetic data, reasons
about the likely root cause, and drafts a structured ticket. Wrapped around the fuzzy
agent is a **deterministic reliability layer**: the output must pass a strict Pydantic
schema, the claimed root cause must be **grounded in evidence the tools actually
returned** (we do not trust the model's self-reported confidence), and anything touching
a **safety-relevant (ASIL-rated) component** is routed to a human regardless of
confidence (an ISO 26262-style safety override). High-confidence, grounded, non-safety
tickets auto-draft; everything else routes to a human queue with the agent's partial
work and a plain-English reason.

Everything is synthetic. No real systems, data, or internal tools.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit .env and paste your real GEMINI_API_KEY
```

## Run

_(Run command will be filled in once the server exists — Part 7.)_

## Project layout

- `data/` — synthetic world: components, logs, known issues, and curated example inputs.
- `app/schema.py` — Pydantic models (the deterministic contract).
- `app/tools.py` — mock tools over the synthetic data, with an evidence trace.
- `app/agent.py` — hand-rolled Gemini tool-calling loop.
- `app/validation.py` — grounding check + decision gate + ISO 26262 safety override.
- `app/pipeline.py` — orchestrates input → agent → validation → decision.
- `app/server.py` — FastAPI app that runs the pipeline and streams the trace.
- `frontend/index.html` — single-page demo UI.

See `BUILD_LOG.md` for a part-by-part account of what was built and why.
