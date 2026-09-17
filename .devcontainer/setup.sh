#!/usr/bin/env bash
# One-time setup for GitHub Codespaces / Dev Containers: installs both apps and builds the frontend.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
echo "==> Backend: creating virtualenv and installing dependencies"
cd "$ROOT/backend"
python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements-dev.txt
echo "==> Frontend: installing dependencies and building"
cd "$ROOT/frontend"
npm ci --no-audit --no-fund --loglevel=error
npm run build
echo "==> Setup complete"
