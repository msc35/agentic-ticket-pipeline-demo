#!/usr/bin/env bash
# Run the app locally AND expose it on a temporary public https:// URL via Cloudflare Tunnel.
# The URL is live only while this script runs — close it (Ctrl+C) and nothing stays online.
#
#   ./scripts/share.sh
#   APP_PASSWORD=somepass ./scripts/share.sh    # protect the public URL with a password
#
# Requires: the .venv set up, GEMINI_API_KEY in .env, and `cloudflared` installed
# (brew install cloudflared).

set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared not found. Install it with:  brew install cloudflared" >&2
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if [ -n "${APP_PASSWORD:-}" ]; then
  echo "🔒 Password protection ON — visitors must enter the password you set in APP_PASSWORD."
else
  echo "⚠️  No APP_PASSWORD set — the public URL will be open. Set APP_PASSWORD to protect it."
fi

PORT=8000
uvicorn app.server:app --host 127.0.0.1 --port "$PORT" --log-level warning &
UVICORN_PID=$!
trap 'kill $UVICORN_PID 2>/dev/null || true' EXIT
sleep 2

echo "Starting Cloudflare tunnel… your public URL appears below (give it ~10-30s to warm up)."
cloudflared tunnel --url "http://localhost:${PORT}"
