.PHONY: setup backend frontend test eval demo build

setup:            ## install backend + frontend dependencies
	cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
	cd frontend && npm install

backend:          ## run the API on :8000
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

frontend:         ## run the web app on :3000
	cd frontend && npm run dev

test:             ## backend test-suite (engine, extraction, reconciliation benchmark, API)
	cd backend && .venv/bin/python -m pytest -q

eval:             ## run the AI evaluation benchmark from the CLI
	cd backend && PYTHONPATH=. .venv/bin/python -c "from app.evaluation.runner import evaluate_all; r=evaluate_all(); print({k:v for k,v in r.items() if k!='cases'})"

build:            ## production build of the frontend
	cd frontend && npm run build
