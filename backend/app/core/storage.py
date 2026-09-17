"""Object storage for uploaded documents. Blobs are encrypted before they are written."""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path

from app.config import get_settings
from app.core.crypto import get_encryptor

SAFE_KEY = re.compile(r"^[a-zA-Z0-9_\-/\.]+$")


class ObjectStorage(ABC):
    @abstractmethod
    def put(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...


class LocalEncryptedStorage(ObjectStorage):
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if not SAFE_KEY.match(key) or ".." in key:
            raise ValueError("invalid storage key")
        p = (self.base_dir / key).resolve()
        if self.base_dir.resolve() not in p.parents:
            raise ValueError("invalid storage key")
        return p

    def put(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(get_encryptor().encrypt(data))

    def get(self, key: str) -> bytes:
        return get_encryptor().decrypt(self._path(key).read_bytes())

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()


class S3EncryptedStorage(ObjectStorage):  # pragma: no cover - requires AWS credentials
    def __init__(self, bucket: str):
        import boto3  # optional dependency

        self.bucket = bucket
        self.client = boto3.client("s3")

    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=get_encryptor().encrypt(data),
                               ServerSideEncryption="AES256")

    def get(self, key: str) -> bytes:
        obj = self.client.get_object(Bucket=self.bucket, Key=key)
        return get_encryptor().decrypt(obj["Body"].read())

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


_storage: ObjectStorage | None = None


def get_storage() -> ObjectStorage:
    global _storage
    if _storage is None:
        s = get_settings()
        if s.astra_storage_backend == "s3" and s.astra_s3_bucket:
            _storage = S3EncryptedStorage(s.astra_s3_bucket)
        else:
            _storage = LocalEncryptedStorage(s.resolve_path(s.astra_storage_dir))
    return _storage
