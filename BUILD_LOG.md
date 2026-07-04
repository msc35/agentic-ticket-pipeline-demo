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
