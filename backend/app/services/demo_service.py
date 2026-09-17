"""Demo taxpayer generation flow shared by the HTTP API and the in-browser backend."""
from __future__ import annotations

from app.core import audit
from app.models.common import SOURCE_LABELS
from app.reconciliation.engine import reconcile
from app.services.errors import ServiceError
from app.synthetic.generator import compute_expected_impacts, generate, hidden_test_payload, materialize
from app.tax_engine.engine import compute
from app.tax_engine.rules import get_rules


def generate_demo(scenario: str, seed: int, assessment_year: str, owner_user_id: str, store):
    """Returns (case, generated, items, computation, hidden_test_payload)."""
    try:
        get_rules(assessment_year)
        gen = generate(scenario, seed, assessment_year)
    except (KeyError, ValueError) as exc:
        raise ServiceError(400, str(exc)) from exc
    case = materialize(gen, owner_user_id=owner_user_id, store=store)
    compute_expected_impacts(case, gen.hidden_issues)
    items = reconcile(case)
    computation = compute(case)
    return case, gen, items, computation, hidden_test_payload(gen)


def record_demo_timeline(repo, case, gen, items, computation, user_id: str) -> None:
    """Replays a realistic audit timeline for a generated case."""
    open_count = len([i for i in items if i.status == "OPEN"])
    audit.record(repo, case.id, user_id, audit.ACTOR_USER, "demo.generated", f"Demo taxpayer generated – scenario '{gen.label}' (seed {gen.seed})", {"scenario": gen.scenario, "seed": gen.seed})
    audit.record(repo, case.id, user_id, audit.ACTOR_USER, "profile.updated", "Onboarding completed", {"fields": ["name", "pan", "date_of_birth", "income_sources"]})
    for d in case.documents:
        audit.record(repo, case.id, user_id, audit.ACTOR_USER, "document.uploaded", f"{SOURCE_LABELS.get(d.type, d.type.value)} uploaded ({d.filename})", {"document_id": d.id})
        audit.record(repo, case.id, None, audit.ACTOR_ASTRA, "document.extracted", f"{SOURCE_LABELS.get(d.type, d.type.value)} processed – {len(d.linked_entity_ids)} value(s) extracted at {int(d.extraction_confidence * 100)}% confidence",
                     {"document_id": d.id, "method": d.extraction_method, "confidence": d.extraction_confidence, "flags": d.flags}, [{"label": d.filename, "document_id": d.id}])
    audit.record(repo, case.id, None, audit.ACTOR_ASTRA, "reconciliation.run", f"Reconciliation run – {open_count} open item(s)", {"open_items": open_count})
    audit.record(repo, case.id, None, audit.ACTOR_SYSTEM, "computation.updated", "Tax calculation updated", {"rules_version": computation.rules_version})
