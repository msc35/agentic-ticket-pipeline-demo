"""FastAPI app: expose the pipeline and stream the reasoning trace to the browser.

Endpoints:
  GET  /examples  -> the curated example inputs (so the UI can offer them as buttons)
  POST /run       -> run the pipeline on a report; streams the trace as Server-Sent Events
  GET  /          -> serve the single-page UI (frontend/index.html)

All state is in-memory per request. CORS is open for local use.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.pipeline import iter_pipeline

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
INDEX_HTML = ROOT / "frontend" / "index.html"

app = FastAPI(title="Agentic Incident-to-Ticket Pipeline")

# Open CORS for local development (the UI may be opened from file:// or another port).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class RunRequest(BaseModel):
    report: str


@app.get("/examples")
def list_examples() -> list[dict]:
    """Return the curated example inputs for the UI's example buttons."""
    examples = json.load(open(DATA_DIR / "example_inputs.json", encoding="utf-8"))
    # Expose only what the UI needs (label + report + a hint of the intended path).
    return [
        {
            "id": e["id"],
            "label": e["label"],
            "report": e["report"],
            "intended_path": e.get("intended_path"),
        }
        for e in examples
    ]


@app.post("/run")
def run(req: RunRequest) -> StreamingResponse:
    """Run the pipeline and stream the trace as Server-Sent Events.

    Each SSE message is a JSON event: agent messages and tool calls as they happen, then a
    final `result` event. Errors (e.g. a missing API key) are streamed as an `error` event so
    the UI can show them instead of the connection just dying.
    """
    report = req.report.strip()

    def event_stream():
        if not report:
            yield _sse({"type": "error", "message": "Report is empty."})
            return
        try:
            for event in iter_pipeline(report):
                yield _sse(event)
        except Exception as e:  # surface any failure to the UI rather than dropping the stream
            yield _sse({"type": "error", "message": f"{type(e).__name__}: {e}"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: dict) -> str:
    """Format one event as an SSE `data:` frame."""
    return f"data: {json.dumps(event)}\n\n"


@app.get("/")
def index():
    """Serve the single-page UI."""
    if INDEX_HTML.exists():
        return FileResponse(INDEX_HTML)
    return JSONResponse({"message": "frontend/index.html not built yet (Part 8)."})
