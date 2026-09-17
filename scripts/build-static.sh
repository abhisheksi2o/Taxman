#!/usr/bin/env bash
# Builds the fully static, in-browser edition of ASTRA Tax (GitHub Pages).
#   BASE_PATH   URL prefix the site is served under (default /Taxman for project pages; "" for a custom domain)
#   PYODIDE     Pyodide release to load from jsDelivr (must ship pydantic 2.x)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE_PATH="${BASE_PATH-/Taxman}"
PYODIDE="${PYODIDE:-0.28.3}"
PY_DIR="$ROOT/frontend/public/py"
mkdir -p "$PY_DIR/wheels"

echo "==> Packaging the Python domain code"
rm -f "$PY_DIR/app.zip"
( cd "$ROOT/backend" && zip -qr "$PY_DIR/app.zip" app -x 'app/__pycache__/*' -x '*/__pycache__/*' -x '*.pyc' -x 'app/api/*' -x 'app/db/*' -x 'app/main.py' )

WHEELS=$(cd "$PY_DIR/wheels" && ls *.whl | tr '\n' ' ')
BUILD="$(date -u +%Y%m%d%H%M%S)-$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo dev)"
python3 - "$PY_DIR/manifest.json" "$PYODIDE" "$BUILD" $WHEELS <<'PY'
import json, sys
out, pyodide, build, *wheels = sys.argv[1:]
json.dump({"pyodide": pyodide, "app": "app.zip", "wheels": wheels, "build": build}, open(out, "w"), indent=2)
print("manifest:", {"pyodide": pyodide, "wheels": wheels, "build": build})
PY

echo "==> Building the static site (base path '${BASE_PATH}')"
cd "$ROOT/frontend"
rm -rf .next out
STATIC_EXPORT=1 NEXT_PUBLIC_STATIC_MODE=1 NEXT_PUBLIC_BASE_PATH="$BASE_PATH" npm run build
touch out/.nojekyll
cp out/404.html out/404.html 2>/dev/null || true
echo "==> Static site ready in frontend/out ($(du -sh out | cut -f1))"
