"""Field/blob encryption at rest (Fernet – AES-128-CBC + HMAC).

The key never leaves the backend environment. In development an ephemeral key is generated and
persisted under backend/data/ so restarts can read existing data; production refuses to start without
an explicit key.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

log = logging.getLogger("astra.crypto")


class Encryptor:
    def __init__(self, key: bytes):
        self._f = Fernet(key)

    def encrypt(self, data: bytes) -> bytes:
        return self._f.encrypt(data)

    def decrypt(self, token: bytes) -> bytes:
        try:
            return self._f.decrypt(token)
        except InvalidToken as exc:  # pragma: no cover - defensive
            raise ValueError("Unable to decrypt stored data with the configured key") from exc

    def encrypt_str(self, s: str) -> bytes:
        return self.encrypt(s.encode("utf-8"))

    def decrypt_str(self, token: bytes) -> str:
        return self.decrypt(token).decode("utf-8")


def fernet_key_from_secret(secret: str) -> bytes:
    """Accept either a real Fernet key (32 url-safe base64 bytes) or any secret string, which is stretched
    into a Fernet key with SHA-256. Lets hosting platforms inject a generated random secret."""
    import base64
    import hashlib

    raw = secret.strip().encode()
    try:
        if len(base64.urlsafe_b64decode(raw + b"=" * (-len(raw) % 4))) == 32 and len(raw) in (43, 44):
            return raw if raw.endswith(b"=") else raw + b"="
    except Exception:  # noqa: BLE001 – fall through to derivation
        pass
    return base64.urlsafe_b64encode(hashlib.sha256(raw).digest())


@lru_cache
def get_encryptor() -> Encryptor:
    settings = get_settings()
    key = settings.astra_encryption_key
    if key:
        return Encryptor(fernet_key_from_secret(key))
    if settings.is_production:
        raise RuntimeError("ASTRA_ENCRYPTION_KEY must be set in production")
    key_file = settings.data_dir / ".dev-encryption-key"
    if key_file.exists():
        return Encryptor(key_file.read_bytes().strip())
    generated = Fernet.generate_key()
    key_file.write_bytes(generated)
    try:
        key_file.chmod(0o600)
    except OSError:  # pragma: no cover
        pass
    log.warning("ASTRA_ENCRYPTION_KEY not set – generated a development key at %s", key_file)
    return Encryptor(generated)
