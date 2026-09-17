#!/usr/bin/env bash
# Starts the API (:8000) and the web app (:3000) in the background every time the container starts.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${TMPDIR:-/tmp}/astra"
mkdir -p "$LOG_DIR"

if [ ! -x "$ROOT/backend/.venv/bin/uvicorn" ] || [ ! -d "$ROOT/frontend/.next" ]; then
  echo "==> First start: running setup"
  bash "$ROOT/.devcontainer/setup.sh"
fi

if ! pgrep -f "uvicorn app.main:app" >/dev/null 2>&1; then
  echo "==> Starting API on :8000 (log: $LOG_DIR/backend.log)"
  (cd "$ROOT/backend" && setsid nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 >"$LOG_DIR/backend.log" 2>&1 </dev/null &)
fi

if ! pgrep -f "next-server" >/dev/null 2>&1 && ! pgrep -f "next start" >/dev/null 2>&1; then
  echo "==> Starting web app on :3000 (log: $LOG_DIR/frontend.log)"
  (cd "$ROOT/frontend" && BACKEND_URL=http://localhost:8000 setsid nohup npm run start >"$LOG_DIR/frontend.log" 2>&1 </dev/null &)
fi

for i in $(seq 1 30); do
  if curl -fs -m 5 http://localhost:3000/login >/dev/null 2>&1 && curl -fs -m 5 http://localhost:8000/api/health >/dev/null 2>&1; then
    echo "==> ASTRA Tax is running. Open the forwarded port 3000 (PORTS tab) and click 'Continue with a demo workspace'."
    exit 0
  fi
  sleep 2
done
echo "==> Servers are still starting; check $LOG_DIR/*.log if port 3000 does not respond within a minute."
exit 0
