"""Authentication primitives: password hashing (scrypt), opaque session tokens, rate limiting."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass, field

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return digest.hex(), salt.hex()


def verify_password(password: str, stored_hash: str, salt_hex: str) -> bool:
    digest, _ = hash_password(password, bytes.fromhex(salt_hex))
    return hmac.compare_digest(digest, stored_hash)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validate_password_strength(password: str) -> str | None:
    if len(password) < 10:
        return "Password must be at least 10 characters long."
    if password.lower() == password or password.upper() == password:
        return "Password must mix upper- and lower-case letters."
    if not any(ch.isdigit() for ch in password):
        return "Password must contain a digit."
    return None


@dataclass
class RateLimiter:
    """Small in-memory sliding-window limiter (per key). Replace with Redis for multi-instance deployments."""

    max_events: int
    window_seconds: int
    _events: dict[str, list[float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            bucket = [t for t in self._events.get(key, []) if now - t < self.window_seconds]
            if len(bucket) >= self.max_events:
                self._events[key] = bucket
                return False
            bucket.append(now)
            self._events[key] = bucket
            return True


login_limiter = RateLimiter(max_events=10, window_seconds=300)
upload_limiter = RateLimiter(max_events=60, window_seconds=300)
astra_limiter = RateLimiter(max_events=40, window_seconds=300)
