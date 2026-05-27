"""server-side keypair + client trust store.

server signing key:
- generated lazily on first use at $EHDS_JWKS_PATH/server.pem (+ .pub).
- key id = sha256(pubkey)[:16], stable across restarts.

client trust store:
- $EHDS_CLIENT_REGISTRY is a json file:
    {
      "clients": [
        {"client_id": "...", "jwks": {"keys": [ ... JWK list ... ]},
         "scopes": ["system/*.read"]}
      ]
    }
- jwks is the client's public JWKS (the server keeps no client private material).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey

from app.config import settings

# ---------- server key ----------

def _generate_server_key(jwks_dir: Path) -> RSAPrivateKey:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks_dir.mkdir(parents=True, exist_ok=True)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    (jwks_dir / "server.pem").write_bytes(pem)
    (jwks_dir / "server.pem").chmod(0o600)
    return key


def server_private_key() -> RSAPrivateKey:
    pem_path = settings.jwks_path / "server.pem"
    if not pem_path.exists():
        return _generate_server_key(settings.jwks_path)
    return serialization.load_pem_private_key(pem_path.read_bytes(), password=None)  # type: ignore[return-value]


def server_public_key() -> RSAPublicKey:
    return server_private_key().public_key()


def _kid_for_public_key(pub: RSAPublicKey) -> str:
    der = pub.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(der).hexdigest()[:16]


def server_kid() -> str:
    return _kid_for_public_key(server_public_key())


def server_jwks() -> dict[str, Any]:
    from jwt.algorithms import RSAAlgorithm
    pub = server_public_key()
    jwk = json.loads(RSAAlgorithm.to_jwk(pub))
    jwk["kid"] = server_kid()
    jwk["use"] = "sig"
    jwk["alg"] = "RS256"
    return {"keys": [jwk]}


# ---------- client registry ----------

@dataclass(frozen=True)
class RegisteredClient:
    client_id: str
    jwks: dict[str, Any]
    scopes: tuple[str, ...]


def load_clients() -> dict[str, RegisteredClient]:
    path = settings.client_registry
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    out: dict[str, RegisteredClient] = {}
    for entry in raw.get("clients", []):
        out[entry["client_id"]] = RegisteredClient(
            client_id=entry["client_id"],
            jwks=entry["jwks"],
            scopes=tuple(entry.get("scopes", ["system/*.read"])),
        )
    return out


def upsert_client(client_id: str, jwks: dict[str, Any], scopes: list[str]) -> None:
    """used by the register_client CLI tool."""
    path = settings.client_registry
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.loads(path.read_text()) if path.exists() else {"clients": []}
    raw.setdefault("clients", [])
    raw["clients"] = [c for c in raw["clients"] if c["client_id"] != client_id]
    raw["clients"].append({"client_id": client_id, "jwks": jwks, "scopes": scopes})
    path.write_text(json.dumps(raw, indent=2, sort_keys=True))
