"""SMART backend services: /.well-known/smart-configuration + /token.

per: https://hl7.org/fhir/smart-app-launch/backend-services.html

token endpoint accepts client_credentials grant with a JWT client assertion
(client_assertion_type = urn:ietf:params:oauth:client-assertion-type:jwt-bearer).
returns an RS256-signed JWT bearer with the requested system/* scopes that the
client is registered for.
"""
from __future__ import annotations

import time
import uuid
from collections import deque
from typing import Any

import jwt
from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import JSONResponse

from app.auth.jwks import load_clients, server_kid, server_private_key, server_jwks
from app.config import settings

router = APIRouter()

# replay-cache: small in-memory deque, OK for single-host demo
_SEEN_JTI: deque[tuple[float, str]] = deque(maxlen=10_000)
_JTI_TTL = 600.0  # 10 min window


def _check_jti_replay(jti: str, exp: float) -> None:
    now = time.time()
    # purge expired
    while _SEEN_JTI and _SEEN_JTI[0][0] < now:
        _SEEN_JTI.popleft()
    for ts, j in _SEEN_JTI:
        if j == jti:
            raise HTTPException(status_code=400, detail={"error": "invalid_client", "error_description": "jti replay"})
    _SEEN_JTI.append((min(exp, now + _JTI_TTL), jti))


def _filter_scopes(requested: str, allowed: tuple[str, ...]) -> list[str]:
    asked = [s for s in requested.split() if s]
    if not asked:
        return list(allowed)
    granted: list[str] = []
    for s in asked:
        for a in allowed:
            if a == s or a.endswith("/*.read") and s.startswith(a.split("/")[0] + "/") and s.endswith(".read"):
                granted.append(s)
                break
    return granted


def _verify_client_assertion(assertion: str) -> tuple[str, list[str]]:
    try:
        unverified = jwt.get_unverified_header(assertion)
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=400, detail={"error": "invalid_client", "error_description": f"bad header: {e}"})
    kid = unverified.get("kid")
    alg = unverified.get("alg", "RS256")
    if alg not in ("RS256", "RS384", "RS512", "ES256", "ES384"):
        raise HTTPException(status_code=400, detail={"error": "invalid_client", "error_description": "alg not allowed"})
    try:
        payload_unverified = jwt.decode(assertion, options={"verify_signature": False})
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=400, detail={"error": "invalid_client", "error_description": f"bad payload: {e}"})
    client_id = payload_unverified.get("iss") or payload_unverified.get("sub")
    if not client_id:
        raise HTTPException(status_code=400, detail={"error": "invalid_client", "error_description": "missing iss/sub"})
    clients = load_clients()
    if client_id not in clients:
        raise HTTPException(status_code=401, detail={"error": "invalid_client", "error_description": "unknown client"})
    client = clients[client_id]

    # find the matching JWK
    keys = client.jwks.get("keys", [])
    candidate = next((k for k in keys if k.get("kid") == kid), None) if kid else None
    if candidate is None and len(keys) == 1:
        candidate = keys[0]
    if candidate is None:
        raise HTTPException(status_code=401, detail={"error": "invalid_client", "error_description": "no matching kid"})
    pubkey = jwt.algorithms.RSAAlgorithm.from_jwk(candidate)  # type: ignore[attr-defined]

    try:
        payload = jwt.decode(
            assertion,
            key=pubkey,
            algorithms=[alg],
            audience=settings.token_endpoint,
            options={"require": ["exp", "iss", "sub", "aud", "jti"]},
        )
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail={"error": "invalid_client", "error_description": str(e)})

    if payload["iss"] != payload["sub"]:
        raise HTTPException(status_code=401, detail={"error": "invalid_client", "error_description": "iss != sub"})
    _check_jti_replay(payload["jti"], float(payload["exp"]))
    return client_id, list(client.scopes)


@router.get("/.well-known/smart-configuration")
def smart_configuration() -> JSONResponse:
    body = {
        "issuer": settings.issuer,
        "jwks_uri": settings.base_url + "/.well-known/jwks.json",
        "token_endpoint": settings.token_endpoint,
        "token_endpoint_auth_methods_supported": ["private_key_jwt"],
        "token_endpoint_auth_signing_alg_values_supported": ["RS256", "RS384", "ES256", "ES384"],
        "grant_types_supported": ["client_credentials"],
        "scopes_supported": [
            "system/*.read",
            "system/Patient.read",
            "system/DocumentReference.read",
            "system/Binary.read",
        ],
        "response_types_supported": ["token"],
        "capabilities": ["client-confidential-asymmetric"],
        "code_challenge_methods_supported": [],
    }
    return JSONResponse(body)


@router.get("/.well-known/jwks.json")
def jwks_json() -> JSONResponse:
    return JSONResponse(server_jwks())


@router.post("/token")
def token(
    grant_type: str = Form(...),
    client_assertion_type: str = Form(...),
    client_assertion: str = Form(...),
    scope: str = Form(""),
) -> JSONResponse:
    if grant_type != "client_credentials":
        raise HTTPException(status_code=400, detail={"error": "unsupported_grant_type"})
    if client_assertion_type != "urn:ietf:params:oauth:client-assertion-type:jwt-bearer":
        raise HTTPException(status_code=400, detail={"error": "invalid_request", "error_description": "wrong client_assertion_type"})

    client_id, allowed_scopes = _verify_client_assertion(client_assertion)
    granted = _filter_scopes(scope, tuple(allowed_scopes))
    if not granted:
        raise HTTPException(status_code=400, detail={"error": "invalid_scope"})

    now = int(time.time())
    bearer_payload: dict[str, Any] = {
        "iss": settings.issuer,
        "sub": client_id,
        "aud": settings.base_url,
        "iat": now,
        "exp": now + settings.token_ttl_seconds,
        "jti": str(uuid.uuid4()),
        "scope": " ".join(granted),
        "client_id": client_id,
    }
    bearer = jwt.encode(
        bearer_payload,
        server_private_key(),
        algorithm="RS256",
        headers={"kid": server_kid()},
    )
    return JSONResponse({
        "access_token": bearer,
        "token_type": "bearer",
        "expires_in": settings.token_ttl_seconds,
        "scope": " ".join(granted),
    })
