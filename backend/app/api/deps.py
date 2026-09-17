"""Request dependencies: DB session, authenticated user, owned case."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.security import hash_token
from app.db.models import Session as DbSession
from app.db.models import User
from app.db.repository import CaseRepository
from app.db.session import get_db
from app.models.tax_model import TaxCase

COOKIE_NAME = "astra_session"
CLIENT_HEADER = "x-astra-client"


def get_repo(db: Session = Depends(get_db)) -> CaseRepository:
    return CaseRepository(db)


def _token_from_request(request: Request) -> tuple[str | None, str]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip(), "bearer"
    return request.cookies.get(COOKIE_NAME), "cookie"


def current_user(request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> User:
    token, via = _token_from_request(request)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    row = db.execute(select(DbSession).where(DbSession.token_hash == hash_token(token))).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if row is None or row.revoked or row.expires_at.replace(tzinfo=timezone.utc) < now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    # CSRF defence for cookie sessions: state-changing requests must carry the custom client header
    if via == "cookie" and request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CLIENT_HEADER) != "web":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing client header")
    if (now - row.last_seen_at.replace(tzinfo=timezone.utc)) > timedelta(minutes=5):
        row.last_seen_at = now
        row.expires_at = now + timedelta(hours=settings.astra_session_ttl_hours)
        db.commit()
    user = db.get(User, row.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown user")
    request.state.user_id = user.id
    return user


def developer_user(user: User = Depends(current_user), settings: Settings = Depends(get_settings)) -> User:
    if not settings.astra_dev_mode or user.role != "developer":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Developer mode is not enabled for this account")
    return user


def load_case(case_id: str, user: User = Depends(current_user), repo: CaseRepository = Depends(get_repo)) -> TaxCase:
    case = repo.get(case_id, user.id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Case not found")
    return case
