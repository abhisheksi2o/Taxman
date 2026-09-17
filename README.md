# ASTRA Tax — AI Income Tax Filing & Reconciliation Agent

A professional tax-filing workspace for Indian income-tax returns with an AI analyst inside it — not a chatbot.

```
collect → extract → reconcile → calculate → detect issues → explain → prepare → verify
```

**Core principle:** the LLM is never the authoritative tax calculator. A deterministic, rule-versioned
tax engine computes every number; the AI layer only understands documents, classifies, investigates
discrepancies, explains, and orchestrates — and every AI conclusion is checked against the structured model.

| Layer | Responsibility | Where |
|---|---|---|
| **Deterministic tax engine** | heads of income, deductions & limits, slabs, rebate 87A (marginal relief), surcharge, cess, TDS/TCS credit ledger, advance-tax interest 234A/B/C, rounding 288A/B, validation | `backend/app/tax_engine/` |
| **Versioned rules** | AY 2025-26 and AY 2026-27 rule sets (slabs, thresholds, CG rates by transfer date, CII, TDS thresholds, due dates, sources) | `backend/app/tax_engine/rules/` |
| **Unified tax data model** | source-agnostic taxpayer profile; every number is a `TracedValue` with status + provenance | `backend/app/models/` |
| **Document intelligence** | detect → extract → normalise → attach with provenance, confidence and ambiguity flags (rule-based parsers; optional Claude extraction for scans) | `backend/app/documents/` |
| **Reconciliation engine** | MISSING / MISMATCH / DUPLICATE / CLASSIFICATION / TIMING across Form 16 · 16A · AIS · TIS · 26AS · bank · broker · previous ITR · profile | `backend/app/reconciliation/` |
| **Before-You-File** | seven checks → readiness score; requirement engine drives onboarding and the document checklist | `backend/app/readiness/` |
| **Evidence graph** | "Why is this number here?" — line → formula → inputs → documents | `backend/app/evidence/` |
| **Ask Astra** | tool-calling investigator (Claude) with a deterministic fallback; every ₹ figure verified against the model | `backend/app/ai/` |
| **Synthetic data + evaluation** | 12 coherent demo scenarios with hidden issues; benchmark of detection, evidence, impact accuracy, hallucination | `backend/app/synthetic/`, `backend/app/evaluation/` |
| **Web app** | Dashboard · My Tax Profile · Documents · Reconciliation · Tax Computation · Issues & Alerts · Ask Astra · Return Review (+ Audit trail, AI Evaluation) | `frontend/` |

## Use it right now (hosted on GitHub Pages)

**https://abhisheksi2o.github.io/Taxman/**

The hosted edition runs the *same* Python tax engine, extraction pipeline, reconciliation, readiness checks and
deterministic Astra inside your browser (Pyodide / WebAssembly) behind the same API contract as the server. Nothing
is uploaded anywhere: your workspace lives in the browser's IndexedDB on this device. The first visit downloads
about 15 MB of runtime; later visits are cached. Astra's Claude-powered mode is server-only, so the hosted edition
always answers in deterministic mode. It is published by `.github/workflows/pages.yml` on every push.

> **Seeing a GitHub 404, or just this README, at that link?** Pages is not publishing from the workflow yet — GitHub only
> lets a repository admin set that, once: **Settings → Pages → Build and deployment → Source: GitHub Actions**
> (not *Deploy from a branch*, which publishes this README instead of the app), then re-run *Deploy to GitHub Pages*
> under **Actions** (or push any commit). From then on every push publishes automatically.

## Other one-click options

| | |
|---|---|
| [![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/abhisheksi2o/Taxman?quickstart=1) | **Run the real app in your browser, nothing to install.** Creates a Codespace on your GitHub account, installs both services and opens the app on the forwarded port 3000 (about 3–5 minutes the first time). Then click *Continue with a demo workspace* → *Generate demo taxpayer*. |
| [![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/abhisheksi2o/Taxman) | **Get a permanent public URL.** Uses `render.yaml` to create the API and web services on Render's free tier under your Render account (first build ≈ 5 minutes; free instances sleep when idle and keep demo data only until they restart). |

Both options run the code in this repository unchanged; the only difference is where it runs.

## Quick start (server edition)

```bash
# backend (Python 3.11+)
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --reload --port 8000      # http://localhost:8000/api/docs

# frontend (Node 20+)
cd frontend
npm install
BACKEND_URL=http://localhost:8000 npm run dev            # http://localhost:3000
```

Open http://localhost:3000, click **Continue with a demo workspace**, then **Generate demo taxpayer** and pick a
scenario (e.g. *Missing income*, *TDS mismatch*, *Mixed income sources*). Or **Start my return** to go through
onboarding and upload your own documents.

Configuration lives in environment variables — see `.env.example` (copy to `backend/.env`). Without
`ANTHROPIC_API_KEY` Astra runs in a fully grounded deterministic mode; with it, Astra uses Claude tool-calling
over the same tools (model configurable via `ASTRA_LLM_MODEL`, default `claude-opus-5`).

`docker compose up --build` starts PostgreSQL + backend + frontend (set `ASTRA_ENCRYPTION_KEY` in `.env` first).

## Tests & benchmark

```bash
cd backend && .venv/bin/python -m pytest -q     # engine, extraction, reconciliation benchmark, API e2e (30 tests)
make eval                                       # prints the AI-evaluation summary for all scenarios
```

The reconciliation benchmark asserts that **every hidden issue in every scenario is detected with the expected
evidence and tax impact, with zero HIGH/MEDIUM false positives** on the clean control scenario, and that Astra's
answers contain no rupee figure that cannot be traced to the model.

## Supported inputs

Form 16 · Form 16A · AIS (JSON) · TIS (JSON) · Form 26AS · salary slips · bank statements (CSV) · interest
certificates · broker tax P&L / capital-gains statements (CSV) · dividend statements (CSV) · rent receipts ·
home-loan interest certificates · previous ITR (JSON) · investment proofs (JSON). Text PDFs are parsed
rule-based; scanned PDFs / images go through Claude structured extraction when a key is configured, otherwise they
are flagged for manual entry. Nothing is ever added to income silently from information statements (AIS / 26AS /
bank statements) — Astra proposes, the taxpayer confirms.

## What the taxpayer sees

* **Every number has a source** — provenance chips on each value; *Extracted / Calculated / User-entered /
  User-confirmed / AI-suggested / Unresolved* labels everywhere.
* **Every calculation has an explanation** — expandable computation tree, formula and rule reference per line,
  "Why is this number here?" drawer, both regimes side by side with a numerical explanation (Astra never picks).
* **Every AI conclusion is grounded** — tool calls and evidence shown under each answer; unverifiable amounts are
  flagged; neutral language ("this item requires review").
* **Every consequential action needs explicit confirmation** — resolving items, confirming values, declarations
  and regime choice before a return package is prepared. The prototype does not e-file.

## Security posture (prototype)

Encrypted-at-rest taxpayer model, PAN and document blobs (Fernet); scrypt password hashing; opaque HttpOnly
session cookies with idle expiry and a CSRF client header; per-user ownership checks on every case/document;
rate limiting on auth, uploads and Astra; security headers; no secrets in the frontend (the browser only talks to
the Next.js proxy); audit log for every important action (no hidden chain-of-thought stored); document deletion
purges extracted values; retention purge for demo cases. See `ARCHITECTURE.md` for details and known gaps.

## Compliance note

ASTRA Tax prepares **estimates** under versioned official rules. It is not a substitute for a tax professional,
it does not file returns, and unresolved items should be reviewed before filing. Rule sets cite their sources and
must be updated when the law or CBDT notifications change.
