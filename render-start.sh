#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-10000}"
WORKERS="${WEB_CONCURRENCY:-1}"
THREADS="${GUNICORN_THREADS:-2}"
TIMEOUT="${GUNICORN_TIMEOUT:-120}"

if command -v gunicorn >/dev/null 2>&1; then
  echo "[Home13] Starting with gunicorn on 0.0.0.0:${PORT}"
  exec gunicorn app:app \
    --bind "0.0.0.0:${PORT}" \
    --workers "${WORKERS}" \
    --threads "${THREADS}" \
    --timeout "${TIMEOUT}"
fi

echo "[Home13] gunicorn not found, fallback to python app.py"
exec python app.py
