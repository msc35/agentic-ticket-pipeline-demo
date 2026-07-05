# Container for the agentic incident-to-ticket pipeline.
# The GEMINI_API_KEY is provided at RUN time as an env var — never baked into the image.

FROM python:3.11-slim

WORKDIR /app

# Install dependencies first so this layer is cached across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy only what the app needs to run.
COPY app ./app
COPY data ./data
COPY frontend ./frontend

# Run as a non-root user.
RUN useradd --create-home appuser
USER appuser

# Cloud hosts (e.g. Cloud Run) inject $PORT; default to 8000 locally.
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.server:app --host 0.0.0.0 --port ${PORT}"]
