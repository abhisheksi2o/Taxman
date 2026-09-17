"""ASTRA Tax backend – FastAPI application."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    routes_astra,
    routes_audit,
    routes_auth,
    routes_cases,
    routes_computation,
    routes_dashboard,
    routes_demo,
    routes_documents,
    routes_eval,
    routes_reconciliation,
    routes_review,
)
from app.config import get_settings
from app.core.crypto import get_encryptor
from app.core.logging_config import configure_logging
from app.db.session import get_engine

log = logging.getLogger("astra")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    get_encryptor()  # fail fast in production if no key
    get_engine()
    log.info("ASTRA Tax backend started (env=%s, llm=%s, demo=%s, dev=%s)", settings.astra_env, settings.llm_available, settings.astra_demo_mode, settings.astra_dev_mode)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="ASTRA Tax API", version="0.1.0", lifespan=lifespan, docs_url="/api/docs" if not settings.is_production else None,
                  redoc_url=None, openapi_url="/api/openapi.json" if not settings.is_production else None)
    app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):  # never leak internals or PII
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal error"})

    api_prefix = "/api"
    for r in (routes_auth, routes_cases, routes_documents, routes_reconciliation, routes_computation, routes_astra, routes_review, routes_demo, routes_eval, routes_audit, routes_dashboard):
        app.include_router(r.router, prefix=api_prefix)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "llm": settings.llm_available, "demo_mode": settings.astra_demo_mode, "dev_mode": settings.astra_dev_mode, "env": settings.astra_env}

    return app


app = create_app()
