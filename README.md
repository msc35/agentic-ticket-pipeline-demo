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

```bash
# from the project root, with the venv active and GEMINI_API_KEY in .env
uvicorn app.server:app --port 8000
```

Then open http://localhost:8000 in a browser. Use `--reload` during development.

Endpoints: `GET /examples` (curated inputs), `POST /run` (runs the pipeline, streams the
reasoning trace as Server-Sent Events), `GET /` (the single-page UI).

## Share it on a temporary public URL (for a demo on another machine)

Run it on your laptop and expose it on a temporary `https://` URL via Cloudflare Tunnel.
The URL is live only while the command runs — close it and nothing stays online.

```bash
# one command (starts the server + the tunnel, prints the public URL):
APP_PASSWORD=pickapassword ./scripts/share.sh
```

`APP_PASSWORD` is optional but recommended for a public URL: when set, the browser prompts
for it once (any username, that password) so nobody who stumbles on the URL can spend your
Gemini quota. Requires `cloudflared` (`brew install cloudflared`). Without the script:
`uvicorn app.server:app --port 8000` in one terminal and
`cloudflared tunnel --url http://localhost:8000` in another.

## Run in Docker

```bash
docker build -t agentic-ticket .
docker run --rm -p 8000:8000 -e GEMINI_API_KEY=your_key agentic-ticket
# optionally add:  -e APP_PASSWORD=pickapassword
```

The key is passed at run time and never baked into the image. The same image deploys to a
container host (e.g. Google Cloud Run, which injects `$PORT`) if you ever want an always-on
URL instead of a laptop tunnel.

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
