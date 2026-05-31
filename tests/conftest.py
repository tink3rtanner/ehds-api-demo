"""global pytest fixtures.

CRITICAL: env-var setup happens at conftest import time (before pytest collects
test modules) because test modules import `app.*` which loads
`app.config.settings` once at import. autouse fixtures run too late.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

# ---------- early env setup ----------
REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_DATA_DIR = REPO_ROOT / "data"

_TEST_ROOT = Path(tempfile.mkdtemp(prefix="ehds_test_"))
_DATA_DIR = _TEST_ROOT / "data"
# Seed deterministically from scripts.seed rather than copying the live data
# dir. Live data accumulates ITI-105 submissions over time, which would
# poison tests that count "the 10-patient panel".
_DATA_DIR.mkdir(parents=True, exist_ok=True)
# import lazily inside this block so the scripts.seed import doesn't trip
# on env vars that haven't been set yet
import sys as _sys

_sys.path.insert(0, str(REPO_ROOT))
from scripts.seed import seed as _seed  # noqa: E402

_seed(_DATA_DIR, clean=True)

# generate test client key (RSA) ONCE per session
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

_TEST_CLIENT_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

from jwt.algorithms import RSAAlgorithm as _RSAAlgorithm

_TEST_CLIENT_JWK = json.loads(_RSAAlgorithm.to_jwk(_TEST_CLIENT_KEY.public_key()))
_TEST_CLIENT_JWK["kid"] = "test-client-key-1"
_TEST_CLIENT_JWK["use"] = "sig"
_TEST_CLIENT_JWK["alg"] = "RS256"

_CLIENT_REGISTRY_PATH = _TEST_ROOT / "clients.json"
_CLIENT_REGISTRY_PATH.write_text(json.dumps({
    "clients": [{
        "client_id": "test-client",
        "jwks": {"keys": [_TEST_CLIENT_JWK]},
        "scopes": [
            "system/*.read", "system/Bundle.write",
            "system/Patient.read", "system/DocumentReference.read",
            "system/Binary.read",
        ],
    }]
}))

_JWKS_PATH = _TEST_ROOT / "keys"
_JWKS_PATH.mkdir(exist_ok=True)

os.environ["EHDS_BASE_URL"] = "http://testserver"
os.environ["EHDS_ISSUER"] = "http://testserver"
os.environ["EHDS_DATA_DIR"] = str(_DATA_DIR)
os.environ["EHDS_CLIENT_REGISTRY"] = str(_CLIENT_REGISTRY_PATH)
os.environ["EHDS_JWKS_PATH"] = str(_JWKS_PATH)
os.environ["EHDS_RATE_LIMIT_PER_MIN"] = "100000"
os.environ["ENV"] = "dev"

# ---------- fixtures ----------
import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest.fixture(scope="session")
def test_root() -> Path:
    return _TEST_ROOT


@pytest.fixture(scope="session")
def test_client_key() -> rsa.RSAPrivateKey:
    return _TEST_CLIENT_KEY


@pytest.fixture(scope="session")
def test_client_jwk() -> dict:
    return _TEST_CLIENT_JWK


@pytest.fixture(scope="session")
def app():
    from app.main import app as _app
    return _app


@pytest_asyncio.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


def _assertion_for(priv: rsa.RSAPrivateKey, **overrides) -> str:
    now = int(time.time())
    payload = {
        "iss": "test-client",
        "sub": "test-client",
        "aud": "http://testserver/token",
        "iat": now,
        "exp": now + 60,
        "jti": str(uuid.uuid4()),
    }
    payload.update(overrides)
    return jwt.encode(
        payload,
        priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        algorithm="RS256",
        headers={"kid": "test-client-key-1"},
    )


@pytest_asyncio.fixture
async def bearer(client: AsyncClient, test_client_key: rsa.RSAPrivateKey) -> str:
    assertion = _assertion_for(test_client_key)
    r = await client.post(
        "/token",
        data={
            "grant_type": "client_credentials",
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": assertion,
            "scope": "system/*.read system/Bundle.write",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture
def auth_headers(bearer: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {bearer}"}


@pytest.fixture
def make_assertion(test_client_key):
    def _factory(**overrides):
        return _assertion_for(test_client_key, **overrides)
    return _factory


# ---- patient slot helpers ----
# Patient ids are now deterministic UUIDs minted from slot labels. Tests use
# these helpers to get the canonical FHIR id for the synthetic-panel slots.

from app.fhir.ids import child_id as _child_id
from app.fhir.ids import patient_id as _patient_id


@pytest.fixture
def pid() -> str:
    """canonical FHIR Patient.id for slot p-001 (Anna Müller)."""
    return _patient_id("p-001")


@pytest.fixture
def pid_for():
    """callable returning Patient.id for any slot label."""
    return _patient_id


@pytest.fixture
def child_id_for():
    """callable returning the deterministic id for a slot-owned child resource.

    usage: ``child_id_for("p-001", "Observation", 5)``
    """
    return _child_id


# ---- java validator availability ----
def _java_actually_runs() -> bool:
    if shutil.which("java") is None:
        return False
    try:
        import subprocess
        r = subprocess.run(["java", "-version"], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


JAVA_AVAILABLE = _java_actually_runs()
VALIDATOR_JAR = REPO_ROOT / ".cache" / "validator_cli.jar"
PROFILE_VALIDATION_AVAILABLE = JAVA_AVAILABLE and VALIDATOR_JAR.exists()

requires_validator = pytest.mark.skipif(
    not PROFILE_VALIDATION_AVAILABLE,
    reason="java validator jar not available (run ./fetch_validator.sh)",
)
