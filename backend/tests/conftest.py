import os
import pathlib
import shutil
import tempfile

import pytest

TMP = pathlib.Path(tempfile.mkdtemp(prefix="astra-test-"))
os.environ.setdefault("ASTRA_ENV", "development")
os.environ["DATABASE_URL"] = f"sqlite:///{TMP}/test.db"
os.environ["ASTRA_STORAGE_DIR"] = str(TMP / "docs")
os.environ["ASTRA_DEMO_MODE"] = "true"
os.environ["ASTRA_DEV_MODE"] = "true"
os.environ["ASTRA_LLM_ENABLED"] = "off"
os.environ["ANTHROPIC_API_KEY"] = ""

from cryptography.fernet import Fernet  # noqa: E402

os.environ["ASTRA_ENCRYPTION_KEY"] = Fernet.generate_key().decode()


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
    shutil.rmtree(TMP, ignore_errors=True)


@pytest.fixture
def demo_client(client):
    """A fresh demo workspace (isolated user) with the CSRF client header set."""
    client.cookies.clear()
    r = client.post("/api/auth/demo", headers={"x-astra-client": "web"})
    assert r.status_code == 200, r.text
    client.headers.update({"x-astra-client": "web"})
    return client
