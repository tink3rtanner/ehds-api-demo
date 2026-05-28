"""Discoverable endpoints for SMART backend services clients + agents.

These live OUTSIDE /ui/api (which is dev-gated and UI-internal). Anything an
agent should reach by following the URLs in /.well-known/smart-configuration
lives here, so production deployments can still expose registration etc.
"""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.config import settings
from app.fhir import document as doc_compile
from app.fhir import store
from app.fhir.ids import SLOT_IDENTIFIER_SYSTEM, bundle_id


def _slot_of(p: dict) -> str | None:
    for ident in p.get("identifier", []) or []:
        if ident.get("system") == SLOT_IDENTIFIER_SYSTEM:
            return ident.get("value")
    return None


def _slot_for_patient_id(patient_uuid: str) -> str | None:
    p = store.read("Patient", patient_uuid)
    return _slot_of(p) if p else None

router = APIRouter()

# allowlist of scopes the public REST registration may grant. Bundle.write
# requires CLI registration (which has filesystem access to the registry).
_ALLOWED_REG_SCOPES = (
    "system/*.read",
    "system/Patient.read",
    "system/Observation.read",
    "system/Condition.read",
    "system/MedicationStatement.read",
    "system/MedicationRequest.read",
    "system/AllergyIntolerance.read",
    "system/Immunization.read",
    "system/Procedure.read",
    "system/DiagnosticReport.read",
    "system/ImagingStudy.read",
    "system/Encounter.read",
    "system/DocumentReference.read",
    "system/Binary.read",
)
_CLIENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9\-_]{1,62}[a-z0-9]$")


@router.get("/register-client", name="register_client_help",
            description="Returns the JSON schema for the client registration request.")
async def register_client_help() -> JSONResponse:
    """Describe how to POST a client registration.

    Following the RFC 7591 (OAuth Dynamic Client Registration) discovery
    pattern: GET tells you the schema, POST does the registration. We don't
    return strict OpenAPI here because the SMART-config / metadata already
    expose the canonical machine forms — this is just human-and-agent
    readable bootstrap copy.
    """
    base = settings.base_url
    return JSONResponse({
        "method": "POST",
        "url": base + "/register-client",
        "content_type": "application/json",
        "request_body_schema": {
            "client_id": "lowercase letters/numbers/hyphens, 2-64 chars (required)",
            "scopes": f"list of strings, subset of {list(_ALLOWED_REG_SCOPES)} (default: ['system/*.read'])",
            "jwk": "single JWK dict (RSA or EC), OR",
            "public_key_pem": "PEM-encoded public key string (one of jwk / public_key_pem is required)",
        },
        "response_body": {
            "client_id": "echoed back",
            "scopes": "echoed back",
            "jwks": "the JWKS the server now associates with this client",
            "registered": True,
            "next_steps": {
                "token_endpoint": settings.token_endpoint,
                "client_assertion_kid": "{client_id}-key-1",
                "audience": settings.token_endpoint,
                "algorithm": "RS256",
                "expires_in_seconds": settings.token_ttl_seconds,
            },
        },
        "example_curl": (
            f"curl -X POST {base}/register-client "
            f"-H 'Content-Type: application/json' "
            f"-d '{{\"client_id\":\"my-app\",\"scopes\":[\"system/*.read\"],\"public_key_pem\":\"-----BEGIN PUBLIC KEY-----...\"}}'"
        ),
        "after_registration": {
            "mint_token": {
                "url": settings.token_endpoint,
                "method": "POST",
                "form_fields": {
                    "grant_type": "client_credentials",
                    "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                    "client_assertion": "<signed JWT — see jwt_claims>",
                    "scope": "space-separated list of granted scopes",
                },
                "jwt_claims": {
                    "iss": "client_id",
                    "sub": "client_id (same as iss)",
                    "aud": settings.token_endpoint,
                    "iat": "now (seconds)",
                    "exp": "now + 60 (or shorter)",
                    "jti": "random UUID (single-use within 10 min)",
                },
                "jwt_header": {"kid": "{client_id}-key-1", "alg": "RS256"},
            },
        },
        "see_also": {
            "smart_configuration": base + "/.well-known/smart-configuration",
            "capability_statement": base + "/metadata",
            "openapi": base + "/openapi.json",
            "jwks": base + "/.well-known/jwks.json",
            "implementer_guide": base + "/ui/#/implement",
        },
    })


@router.post("/register-client", name="register_client",
             description="Register a SMART backend services client (read-only scopes).")
async def register_client(payload: dict) -> JSONResponse:
    """POST a client registration. Same logic as the /ui/api/register-client
    endpoint but always available (not dev-gated) so production agents can
    also self-register via the URL advertised by smart-configuration."""
    from app.auth.jwks import load_clients, upsert_client

    cid = (payload.get("client_id") or "").strip()
    if not _CLIENT_ID_RE.match(cid):
        raise HTTPException(status_code=400, detail="client_id must match [a-z0-9][a-z0-9-_]{1,62}[a-z0-9]")
    scopes = payload.get("scopes") or ["system/*.read"]
    if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
        raise HTTPException(status_code=400, detail="scopes must be a list of strings")
    bad = [s for s in scopes if s not in _ALLOWED_REG_SCOPES]
    if bad:
        raise HTTPException(status_code=400,
                            detail=f"scopes not allowed via REST: {bad}. allowed: {list(_ALLOWED_REG_SCOPES)}. "
                                   f"system/Bundle.write requires the CLI tool.")

    if payload.get("jwk"):
        jwk = dict(payload["jwk"])
        if not jwk.get("kty"):
            raise HTTPException(status_code=400, detail="jwk.kty missing")
        jwk.setdefault("use", "sig")
        jwk.setdefault("alg", "RS256")
        jwk.setdefault("kid", f"{cid}-key-1")
        jwks = {"keys": [jwk]}
    elif payload.get("public_key_pem"):
        try:
            from cryptography.hazmat.primitives import serialization
            from jwt.algorithms import RSAAlgorithm
            pub = serialization.load_pem_public_key(payload["public_key_pem"].encode())
            jwk = json.loads(RSAAlgorithm.to_jwk(pub))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"bad PEM: {e}")
        jwk["kid"] = f"{cid}-key-1"
        jwk["use"] = "sig"
        jwk["alg"] = "RS256"
        jwks = {"keys": [jwk]}
    else:
        raise HTTPException(status_code=400, detail="provide either jwk or public_key_pem")

    try:
        upsert_client(cid, jwks, scopes)
    except (OSError, PermissionError) as e:
        raise HTTPException(status_code=500, detail=f"could not persist client: {e}")

    loaded = load_clients().get(cid)
    return JSONResponse({
        "client_id": cid,
        "scopes": list(scopes),
        "jwks": jwks,
        "registered": bool(loaded),
        "next_steps": {
            "token_endpoint": settings.token_endpoint,
            "client_assertion_kid": jwks["keys"][0]["kid"],
            "audience": settings.token_endpoint,
            "algorithm": "RS256",
            "expires_in_seconds": settings.token_ttl_seconds,
        },
    })


@router.get("/spec/bundle-id/{pid}/{category}", name="spec_bundle_id")
async def spec_bundle_id(pid: str, category: str) -> JSONResponse:
    """Resolve a (patient, category) pair to the deterministic /Bundle/{uuid}.

    `pid` accepts either a Patient FHIR id (uuid) or a slot identifier
    (``p-001`` etc.) — the latter so agents can derive a canonical URL
    without first calling /Patient?identifier= to translate.
    """
    if category not in doc_compile.CATEGORY_TO_DOC_TYPE:
        raise HTTPException(status_code=404, detail="unknown category")
    # accept either a Patient.id (uuid) or a slot identifier
    patient = store.read("Patient", pid)
    if patient is not None:
        slot = _slot_of(patient) or pid
    else:
        slot = pid
        match = [p for p in store.list_all("Patient") if _slot_of(p) == pid]
        if not match:
            raise HTTPException(status_code=404, detail="patient not found")
        patient = match[0]
    bid = bundle_id(slot, category)
    return JSONResponse({
        "patient": patient["id"],
        "slot": slot,
        "category": category,
        "bundle_id": bid,
        "path": f"/Bundle/{bid}",
        "url": settings.base_url + f"/Bundle/{bid}",
    })


@router.get("/spec/all-bundle-ids", name="spec_all_bundle_ids")
async def spec_all_bundle_ids() -> JSONResponse:
    """Every (patient, category, /Bundle/{uuid}) mapping in one shot."""
    bundles = []
    for p in store.list_all("Patient"):
        slot = _slot_of(p) or p["id"]
        for cat in doc_compile.CATEGORY_TO_DOC_TYPE:
            bid = bundle_id(slot, cat)
            bundles.append({
                "patient": p["id"],
                "slot": slot,
                "category": cat,
                "bundle_id": bid,
                "path": f"/Bundle/{bid}",
                "url": settings.base_url + f"/Bundle/{bid}",
            })
    return JSONResponse({"total": len(bundles), "bundles": bundles})
