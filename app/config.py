"""runtime config sourced from env vars.

design choice: read once at import time into a frozen settings object. tests
that need a different config use the env-overrides fixture in conftest before
importing app.main.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _path(env: str, default: str) -> Path:
    return Path(os.environ.get(env, default)).expanduser().resolve()


@dataclass(frozen=True)
class Settings:
    base_url: str
    issuer: str
    data_dir: Path
    client_registry: Path
    jwks_path: Path
    validator_jar: Path
    audit_log_dir: Path
    audit_retention_days: int
    env: str            # "dev" | "prod"
    rate_limit_per_min: int
    body_max_bytes: int
    token_ttl_seconds: int

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    @property
    def token_endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/token"


def load() -> Settings:
    root = Path(__file__).resolve().parent.parent
    data_dir = _path("EHDS_DATA_DIR", str(root / "data"))
    return Settings(
        base_url=os.environ.get("EHDS_BASE_URL", "http://localhost:8000").rstrip("/"),
        issuer=os.environ.get("EHDS_ISSUER", os.environ.get("EHDS_BASE_URL", "http://localhost:8000")).rstrip("/"),
        data_dir=data_dir,
        client_registry=_path("EHDS_CLIENT_REGISTRY", str(root / "config" / "clients.json")),
        jwks_path=_path("EHDS_JWKS_PATH", str(root / "config" / "server_keys")),
        validator_jar=_path("EHDS_VALIDATOR_JAR", str(root / ".cache" / "validator_cli.jar")),
        audit_log_dir=_path("EHDS_AUDIT_LOG_DIR", str(data_dir / "audit")),
        audit_retention_days=int(os.environ.get("EHDS_AUDIT_RETENTION_DAYS", "30")),
        env=os.environ.get("ENV", "dev"),
        rate_limit_per_min=int(os.environ.get("EHDS_RATE_LIMIT_PER_MIN", "240")),
        body_max_bytes=int(os.environ.get("EHDS_BODY_MAX_BYTES", str(5 * 1024 * 1024))),
        token_ttl_seconds=int(os.environ.get("EHDS_TOKEN_TTL_SECONDS", "900")),
    )


settings = load()
