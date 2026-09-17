"""Authentication: register, login, logout, me, demo workspace."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
import re

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import COOKIE_NAME, current_user
from app.config import Settings, get_settings
from app.core.security import hash_password, hash_token, login_limiter, new_session_token, validate_password_strength, verify_password
from app.db.models import Session as DbSession
from app.db.models import User
from app.db.session import get_db
from app.models.common import new_id

router = APIRouter(prefix="/auth", tags=["auth"])


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Credentials(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=200)
    display_name: str | None = Field(default=None, max_length=120)

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not EMAIL_RE.match(v):
            raise ValueError("Enter a valid email address")
        return v


def _issue_session(db: Session, user: User, response: Response, request: Request, settings: Settings) -> str:
    token = new_session_token()
    now = datetime.now(timezone.utc)
    db.add(DbSession(id=new_id("sess"), user_id=user.id, token_hash=hash_token(token), created_at=now, expires_at=now + timedelta(hours=settings.astra_session_ttl_hours),
                     last_seen_at=now, user_agent=(request.headers.get("user-agent") or "")[:255]))
    user.last_login_at = now
    db.commit()
    response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", secure=settings.astra_cookie_secure, max_age=settings.astra_session_ttl_hours * 3600, path="/")
    return token


def _user_view(user: User, settings: Settings) -> dict:
    return {"id": user.id, "email": user.email, "display_name": user.display_name, "role": user.role, "is_demo": user.is_demo,
            "dev_mode": settings.astra_dev_mode and user.role == "developer", "llm_enabled": settings.llm_available}


@router.post("/register", status_code=201)
def register(body: Credentials, request: Request, response: Response, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    if not login_limiter.allow(f"register:{request.client.host if request.client else 'x'}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts – try again later")
    problem = validate_password_strength(body.password)
    if problem:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, problem)
    if db.execute(select(User).where(User.email == body.email.lower())).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    pw_hash, salt = hash_password(body.password)
    user = User(id=new_id("usr"), email=body.email.lower(), password_hash=pw_hash, password_salt=salt, display_name=body.display_name or body.email.split("@")[0],
                role="developer" if settings.astra_dev_mode else "taxpayer")
    db.add(user)
    db.commit()
    _issue_session(db, user, response, request, settings)
    return _user_view(user, settings)


@router.post("/login")
def login(body: Credentials, request: Request, response: Response, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    key = f"login:{request.client.host if request.client else 'x'}:{body.email.lower()}"
    if not login_limiter.allow(key):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts – try again later")
    user = db.execute(select(User).where(User.email == body.email.lower())).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash, user.password_salt):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    _issue_session(db, user, response, request, settings)
    return _user_view(user, settings)


@router.post("/demo")
def demo_login(request: Request, response: Response, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    """Creates an isolated demo workspace (a throw-away account) – only when ASTRA_DEMO_MODE is on."""
    if not settings.astra_demo_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Demo mode is disabled")
    if not login_limiter.allow(f"demo:{request.client.host if request.client else 'x'}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts – try again later")
    uid = new_id("usr")
    pw_hash, salt = hash_password(new_session_token())
    user = User(id=uid, email=f"{uid}@demo.astra.local", password_hash=pw_hash, password_salt=salt, display_name="Demo taxpayer", role="developer" if settings.astra_dev_mode else "taxpayer", is_demo=True)
    db.add(user)
    db.commit()
    _issue_session(db, user, response, request, settings)
    return _user_view(user, settings)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db), user: User = Depends(current_user)):
    token = request.cookies.get(COOKIE_NAME) or (request.headers.get("authorization", "")[7:] if request.headers.get("authorization", "").lower().startswith("bearer ") else "")
    if token:
        row = db.execute(select(DbSession).where(DbSession.token_hash == hash_token(token))).scalar_one_or_none()
        if row:
            row.revoked = True
            db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user), settings: Settings = Depends(get_settings)):
    return _user_view(user, settings)
