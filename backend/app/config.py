"""Application settings. All secrets come from the environment – never from code or the frontend."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field

BACKEND_DIR = Path(__file__).resolve().parent.parent

try:  # server: full settings with .env support
    from pydantic_settings import BaseSettings, SettingsConfigDict

    _SETTINGS_CONFIG = SettingsConfigDict(env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore")
except ImportError:  # browser build (Pyodide): plain pydantic model fed from os.environ
    import os

    from pydantic import BaseModel, ConfigDict

    _SETTINGS_CONFIG = ConfigDict(extra="ignore", populate_by_name=True)

    class BaseSettings(BaseModel):  # type: ignore[no-redef]
        def __init__(self, **values):
            env = {}
            for name, field in self.model_fields.items():
                for key in {name, name.upper(), field.alias or name}:
                    if key and key in os.environ:
                        env[name] = os.environ[key]
                        break
            super().__init__(**{**env, **values})


class Settings(BaseSettings):
    model_config = _SETTINGS_CONFIG

    astra_env: Literal["development", "production"] = "development"
    astra_encryption_key: str | None = None
    database_url: str = "sqlite:///./data/astra.db"

    astra_storage_backend: Literal["local", "s3"] = "local"
    astra_storage_dir: str = "./data/documents"
    astra_s3_bucket: str | None = None

    astra_session_ttl_hours: int = 12
    astra_cookie_secure: bool = False
    astra_allowed_origins: str = "http://localhost:3000"
    astra_retention_days: int = 30

    astra_demo_mode: bool = True
    astra_dev_mode: bool = True

    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    astra_llm_model: str = "claude-opus-5"
    astra_llm_enabled: Literal["auto", "on", "off"] = "auto"

    astra_max_upload_mb: int = 15

    @property
    def is_production(self) -> bool:
        return self.astra_env == "production"

    @property
    def allowed_origins(self) -> list[str]:
        return [o.strip() for o in self.astra_allowed_origins.split(",") if o.strip()]

    @property
    def data_dir(self) -> Path:
        d = BACKEND_DIR / "data"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def resolve_path(self, p: str) -> Path:
        path = Path(p)
        if not path.is_absolute():
            path = BACKEND_DIR / path
        return path

    @property
    def llm_available(self) -> bool:
        if self.astra_llm_enabled == "off":
            return False
        return bool(self.anthropic_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
