# ASTRA Tax — architecture notes

## 1. Shape

Modular monolith, two deployables:

* `backend/` — FastAPI (Python 3.11). Domain modules are plain Python packages with no web dependencies
  (`models`, `tax_engine`, `reconciliation`, `documents`, `readiness`, `evidence`, `ai`, `synthetic`,
  `evaluation`); `api/` is a thin HTTP layer; `db/` persists the case as an encrypted JSON blob.
* `frontend/` — Next.js 15 (app router, TypeScript, Tailwind v4, recharts). The browser only ever talks to the
  Next.js server, which proxies `/api/*` to the backend (first-party cookie, no backend URL or key in the bundle).

Data: PostgreSQL in production (`DATABASE_URL`), SQLite for local development; documents in encrypted object
storage (local directory or S3 adapter).

## 2. Unified tax data model (`app/models/tax_model.py`)

`TaxCase` is the root (one per taxpayer per assessment year). It holds the profile, income by head
(salary, interest, dividend, rental, capital gains, business, other, exempt), the TDS/TCS ledger, tax payments,
deductions, investments, loans, bank accounts, documents, reconciliation items and review state.

Every financial number is a `TracedValue { amount, status, provenance[] }`. `status` is one of
EXTRACTED · CALCULATED · USER_ENTERED · USER_CONFIRMED · AI_SUGGESTED · UNRESOLVED; each `Provenance` points at a
document id + reference (e.g. "Part B · 1(d)", "Transaction #1847", "AIS item A92") with a confidence.
Document-specific formats never enter the model — extractors translate into these types.

The TDS ledger deliberately keeps one `TaxDeducted` row **per source** (Form 16, 26AS, AIS …). The engine picks the
creditable figure with a transparent priority (user-confirmed › 26AS › Form 16/16A › AIS › user) and records the
choice and any conflict in the computation's `credit_ledger`; reconciliation raises the mismatch.

## 3. Deterministic tax engine (`app/tax_engine/`)

* `rules/base.py` defines the rule schema; `rules/ay_2025_26.py` and `rules/ay_2026_27.py` are pure data
  (slabs per regime and age band, standard deduction, 87A thresholds and marginal relief, surcharge bands and the
  15% cap on special-rate income, cess, LTCG 112A exemption, capital-gains rates by bucket and *transfer date*
  (the 23 July 2024 change), holding-period rules, section 50AA cut-off, CII table, Chapter VI-A limits,
  presumptive thresholds, 234A/B/C rates and instalment schedule, TDS thresholds, due dates and sources).
  `get_rules(ay)` refuses unknown years — nothing is hard-coded in the engine.
* `engine.py` computes both regimes and returns an explanation tree of `Line`s: id, label, amount, kind,
  formula, rule reference, status, sources (provenance of inputs), notes, children. Heads: salary (16(ia), 16(iii),
  HRA from rent paid when known), house property (self-occupied cap, 30% deduction, 71(3A) set-off limit), capital
  gains (classification per transaction, intra-head set-off highest rate first, indexed-vs-flat option for
  pre-23-Jul-2024 property, 112A exemption never taxed at slab rates), business (44ADA/44AD/regular), other
  sources (57(iia)); Chapter VI-A with limits (80CCE aggregate, 80D age bands, auto 80TTA/80TTB entitlement);
  288A rounding; slab tax + special-rate tax with the unexhausted basic-exemption adjustment for residents;
  87A with marginal relief; surcharge with marginal relief; cess; credits; 234A/B/C (Rule 119A rounding, senior
  citizen exemption, presumptive single instalment); 288B rounding.
* `explain_line()` returns the line, its path, its inputs (with provenance rolled up from descendants) for the
  "Why is this number here?" panel.

## 4. Document intelligence (`app/documents/`)

`ingest()` sniffs the MIME type, extracts text (pypdf for PDFs), detects the document type by content
signatures (+ filename + optional user hint), runs the rule-based extractor, and attaches results to the model:

* *income documents* (Form 16, interest certificate, broker / dividend statements, loan certificate, previous
  ITR, investment proof) create entities with status EXTRACTED;
* *information statements* (AIS, TIS, 26AS, bank statements, salary slips) only feed the TDS ledger and emit
  normalised **observations** for reconciliation — income is never added silently from them.

Each `DocumentRecord` keeps the extraction method, confidence (share of required fields found, penalised by
flags), ambiguity flags (e.g. quarterly rows not adding up, duplicated statement rows, out-of-period credits),
linked entity ids and a short text excerpt for "View source" (never the full document).
`llm_extractor.py` (optional, needs `ANTHROPIC_API_KEY`) sends scanned PDFs / images to Claude with a strict
JSON schema; results are marked AI_SUGGESTED and must be confirmed.

## 5. Reconciliation (`app/reconciliation/engine.py`)

All observations and ledger rows are keyed by a normalised counterparty name; TANs are mapped onto the same key so
Form 16, 26AS, AIS, certificates and statements line up. Checks: salary (missing employer, AIS vs Form 16),
interest (per bank × kind: missing from bank/AIS/26AS-194A, AIS vs return, duplicates in the return and in a
statement, out-of-period credits), dividends, capital gains (AIS vs broker per ISIN, ambiguous fund category,
holding period near the threshold, AIS sales with no trades), rent (AIS / 194-IB / recurring credits), business
receipts (per-client attribution of a shortfall, lump-sum declarations), TDS ledger conflicts, TIS totals, previous
year, profile consistency. Items carry neutral wording, evidence refs, entity ids, a deterministic impact estimate
(`impact.tax_delta` re-runs the engine on a copy with the suggested resolution applied) and concrete resolutions.
Fingerprints keep RESOLVED/DISMISSED state across re-runs.

## 6. Readiness & requirements (`app/readiness/`)

`requirements_for(case)` derives required / recommended documents and information from the profile and model
(drives onboarding, the checklist and Astra). `check()` runs the seven Before-You-File checks (completeness,
consistency, documents, calculations, TDS, deductions, profile) with weights → readiness score, counts and
blocking checks.

## 7. Ask Astra (`app/ai/`)

`tools.py` exposes 14 read-only tools over the case, computation, items and readiness report (strict JSON
schemas, Anthropic tool format). `astra.py` runs either a Claude tool-calling loop (`claude-opus-5` by default,
prompt-cached system prompt, refusal / max-token handling) or a deterministic intent router that answers from the
same tools with templates. `_finalize()` collects every amount from the tool outputs and the computation and flags
any rupee figure in the answer that is not among them (`grounding.unverified_amounts`). Conversation history is
stored encrypted per case; audit events record tools and evidence, never chain-of-thought.

## 8. Synthetic data & evaluation (`app/synthetic/`, `app/evaluation/`)

`WorldBuilder` creates a coherent taxpayer world (employers → Form 16 with TDS computed by the engine → 26AS
quarters → AIS; banks → savings/FD interest → statement credits, certificate, AIS SFT, 194A TDS; broker → trades →
capital gains → AIS sale entries; dividends; tenant with 194-IB; home loan; investments; previous ITR). Documents
are rendered in the exact formats the extractors parse (text PDFs, CSV, JSON), so the demo exercises the real
pipeline. Twelve scenarios inject hidden issues and record the correct answer, expected evidence and impact mode.
`materialize()` builds the taxpayer's *declared* case through the pipeline; `evaluate_scenario()` scores detection,
evidence, impact accuracy (correct impact = engine delta of the correct fix), false positives and Astra Q&A
(must-mention facts + hallucination check). Hidden tests are stored encrypted, separately from the model, and are
never visible to Astra's tools.

## 9. Security

* **Encryption at rest**: case model, hidden tests, conversation and document blobs are Fernet-encrypted;
  key from `ASTRA_ENCRYPTION_KEY` (mandatory in production).
* **Auth/session**: scrypt password hashing, opaque random tokens stored hashed with idle expiry and revocation,
  HttpOnly/SameSite=Lax cookies, custom `x-astra-client` header required on mutating cookie requests (CSRF),
  bearer tokens for API clients, rate limiting on auth/upload/Astra.
* **Access control**: every case/document route resolves ownership; developer-only routes gated by role +
  `ASTRA_DEV_MODE`; demo login only when `ASTRA_DEMO_MODE`.
* **Data minimisation**: PAN masked in every response; document excerpts capped; logs never contain bodies and
  mask PAN-like tokens; deleting a document purges its values; case deletion purges blobs; retention purge job.
* **Transport / headers**: security headers on both servers; CORS restricted; API docs disabled in production.
* Known gaps for a production launch: MFA, KMS-backed key rotation, antivirus scanning on upload, Redis-backed
  rate limiting, per-field encryption with separate keys, formal DPDP data-subject workflows.

## 10. Priorities delivered

P0: onboarding, upload, extraction, unified model, reconciliation, deterministic calculation, issue detection,
Ask Astra, Before-You-File, synthetic demo data. P1: evidence graph, previous-year comparison, regime scenario
comparison, evaluation framework. P2 (integrations, CA dashboard, multi-user) is out of scope for this prototype;
the model and API are designed so a filing-platform integration would consume the confirmed return package.
