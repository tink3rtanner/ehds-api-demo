"""Discoverable endpoints for SMART backend services clients + agents.

These live OUTSIDE /ui/api (which is dev-gated and UI-internal). Anything an
agent should reach by following the URLs in /.well-known/smart-configuration
lives here, so production deployments can still expose registration etc.
"""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.auth import scopes as scope_syntax
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

# Scopes advertised as `scopes_supported`. Registration accepts ANY valid
# SMART system scope (v1 `.read/.write/.*` or v2 `.cruds` subsets, specific
# type or `*`); this list is the menu, not a gate. Synthetic-data demo: anyone
# may self-register for read and publish.
_ALLOWED_REG_SCOPES = (
    # broad
    "system/*.rs",
    "system/*.read",
    "system/*.write",
    # what the HL7 EU Health Data API IG names per actor
    "system/Patient.rs",
    "system/DocumentReference.rs",
    "system/DocumentReference.c",   # ITI-105 Simplified Publish
    "system/Bundle.r",
    "system/Binary.r",
    "system/AllergyIntolerance.rs",
    "system/Condition.rs",
    "system/Observation.rs",
    "system/DiagnosticReport.rs",
    "system/ImagingStudy.rs",
    "system/MedicationRequest.rs",
    "system/MedicationDispense.rs",
    "system/MedicationStatement.rs",
    "system/Immunization.rs",
    "system/Encounter.rs",
    "system/Practitioner.r",
    "system/Organization.r",
    # v1 forms still accepted
    "system/DocumentReference.write",
    "system/Bundle.write",
)


def _require_owner(request: Request, client_id: str) -> None:
    """RFC 7592 management needs proof you are that client: a bearer token
    this server issued to it (any scope)."""
    from app.auth.jwks import load_clients
    from app.auth.verify import verify_bearer
    if client_id not in load_clients():
        raise HTTPException(status_code=404, detail=f"client_id not registered: {client_id}")
    principal = verify_bearer(request)
    if principal.client_id != client_id:
        raise HTTPException(status_code=403, detail=f"this token belongs to {principal.client_id}, not {client_id}")


def _invalid_scopes(scopes: list[str]) -> list[str]:
    return [s for s in scopes if not scope_syntax.is_valid(s)]


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
        "create": {
            "method": "POST",
            "url": base + "/register-client",
            "content_type": "application/json",
        },
        "manage": {
            "url_template": base + "/register-client/{client_id}",
            "operations": {
                "GET":    "inspect current registration (scopes, kids, jwks)",
                "PATCH":  "partial update — body may include 'scopes' and/or "
                          "'public_key_pem' (or 'jwk'). use this to add a "
                          "scope (e.g. system/DocumentReference.c) or rotate a key.",
                "PUT":    "full replace — same payload as POST",
                "DELETE": "unregister",
            },
            "auth": "Authorization: Bearer <a token issued to this client> (GET needs none)",
            "example_add_write_scope": (
                f"curl -X PATCH {base}/register-client/<my-client-id> "
                f"-H 'Authorization: Bearer <token>' -H 'Content-Type: application/json' "
                f"-d '{{\"scopes\":[\"system/*.rs\",\"system/DocumentReference.c\"]}}'"
            ),
        },
        "request_body_schema": {
            "client_id": "3-64 chars: lowercase letters, digits, - and _, starting and ending with a letter or digit (required, must be new: 409 if taken)",
            "scopes": "list of SMART system scopes, v1 or v2 syntax, e.g. ['system/*.rs', 'system/DocumentReference.c'] (default: ['system/*.read'])",
            "jwk": "single JWK dict (RSA or EC), OR",
            "public_key_pem": "PEM-encoded public key string (RSA or EC; alg auto-detected from key type)",
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
            "implementer_guide": base + "/llms.txt",
        },
    })


@router.post("/register-client", name="register_client",
             description="Register a SMART backend services client (any system scopes, read and publish).")
async def register_client(payload: dict, _replace: bool = False) -> JSONResponse:
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
    bad = _invalid_scopes(scopes)
    if bad:
        raise HTTPException(status_code=400,
                            detail=f"not valid SMART system scopes: {bad}. use system/<Type|*>.<read|write|cruds subset>, e.g. {list(_ALLOWED_REG_SCOPES[:4])}")

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
            from cryptography.hazmat.primitives.asymmetric import ec, rsa
            from jwt.algorithms import ECAlgorithm, RSAAlgorithm
            pub = serialization.load_pem_public_key(payload["public_key_pem"].encode())
            if isinstance(pub, rsa.RSAPublicKey):
                jwk = json.loads(RSAAlgorithm.to_jwk(pub))
                default_alg = payload.get("alg", "RS256")
            elif isinstance(pub, ec.EllipticCurvePublicKey):
                jwk = json.loads(ECAlgorithm.to_jwk(pub))
                # default to ES256 unless caller asks otherwise (and the
                # curve matches: P-256 -> ES256, P-384 -> ES384)
                curve_alg = {"P-256": "ES256", "P-384": "ES384", "P-521": "ES512"}
                default_alg = payload.get("alg") or curve_alg.get(jwk.get("crv"), "ES256")
            else:
                raise HTTPException(status_code=400, detail=f"unsupported key type: {type(pub).__name__}")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"bad PEM: {e}")
        jwk["kid"] = f"{cid}-key-1"
        jwk["use"] = "sig"
        jwk["alg"] = default_alg
        jwks = {"keys": [jwk]}
    else:
        raise HTTPException(status_code=400, detail="provide either jwk or public_key_pem")

    if not _replace and cid in load_clients():
        raise HTTPException(status_code=409, detail=(
            f"client_id {cid} is already registered. Pick another client_id, or manage this one at "
            f"{settings.base_url}/register-client/{cid} (PATCH/PUT/DELETE) with a bearer token issued to it."))

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
        # RFC 7592: hand the client its own management URL so it can later
        # GET / PATCH / PUT / DELETE its registration without operator help.
        "registration_client_uri": f"{settings.base_url}/register-client/{cid}",
        "next_steps": {
            "token_endpoint": settings.token_endpoint,
            "client_assertion_kid": jwks["keys"][0]["kid"],
            "audience": settings.token_endpoint,
            "algorithm": jwks["keys"][0].get("alg", "RS256"),
            "expires_in_seconds": settings.token_ttl_seconds,
            "manage_registration": {
                "url": f"{settings.base_url}/register-client/{cid}",
                "read":   "GET    (inspect current scopes / kids / jwks)",
                "update": "PATCH  body={\"scopes\":[...]} or {\"public_key_pem\":\"...\"}",
                "replace":"PUT    body=full registration payload",
                "delete": "DELETE (no body)",
            },
        },
    })


@router.get("/register-client/{client_id}", name="register_client_read",
            description="Inspect an existing client registration (no auth in demo mode).")
async def register_client_read(client_id: str) -> JSONResponse:
    """Return the current registration for a client_id.

    Returns the registered scopes, the JWKS (public keys only — there's no
    private material on the server), and the kids the agent should use in
    JWT assertions. Returns 404 if the client_id isn't registered.
    """
    from app.auth.jwks import load_clients
    if not _CLIENT_ID_RE.match(client_id):
        raise HTTPException(status_code=400, detail="bad client_id")
    clients = load_clients()
    c = clients.get(client_id)
    if c is None:
        raise HTTPException(status_code=404, detail=f"client_id not registered: {client_id}")
    return JSONResponse({
        "client_id": c.client_id,
        "scopes": list(c.scopes),
        "jwks": c.jwks,
        "kids": [k.get("kid") for k in c.jwks.get("keys", []) if k.get("kid")],
    })


@router.patch("/register-client/{client_id}", name="register_client_patch",
              description="Update an existing client's scopes and/or rotate its key.")
async def register_client_patch(client_id: str, payload: dict, request: Request) -> JSONResponse:
    """Partial update: change scopes and/or add/replace the public key.

    body fields (all optional, at least one required):
      - scopes: list[str]  -> replace the granted scopes (any valid SMART
                              system scope, v1 or v2 syntax)
      - jwk:   dict        -> replace the JWKS with this single JWK
      - public_key_pem: str -> PEM, converted to JWK and stored

    needs a bearer token issued to this client (RFC 7592 style).
    """
    from app.auth.jwks import patch_client
    if not _CLIENT_ID_RE.match(client_id):
        raise HTTPException(status_code=400, detail="bad client_id")
    _require_owner(request, client_id)
    new_scopes = None
    new_jwks = None
    if "scopes" in payload:
        scopes = payload.get("scopes") or []
        if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
            raise HTTPException(status_code=400, detail="scopes must be a list of strings")
        bad = _invalid_scopes(scopes)
        if bad:
            raise HTTPException(status_code=400,
                                detail=f"not valid SMART system scopes: {bad}. use system/<Type|*>.<read|write|cruds subset>, e.g. {list(_ALLOWED_REG_SCOPES[:4])}")
        new_scopes = list(scopes)
    if payload.get("jwk"):
        jwk = dict(payload["jwk"])
        if not jwk.get("kty"):
            raise HTTPException(status_code=400, detail="jwk.kty missing")
        jwk.setdefault("use", "sig")
        jwk.setdefault("alg", "RS256")
        jwk.setdefault("kid", f"{client_id}-key-1")
        new_jwks = {"keys": [jwk]}
    elif payload.get("public_key_pem"):
        try:
            from cryptography.hazmat.primitives import serialization
            from jwt.algorithms import ECAlgorithm, RSAAlgorithm
            pub = serialization.load_pem_public_key(payload["public_key_pem"].encode())
            try:
                jwk = json.loads(RSAAlgorithm.to_jwk(pub))
            except Exception:
                jwk = json.loads(ECAlgorithm.to_jwk(pub))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"bad PEM: {e}")
        jwk["kid"] = f"{client_id}-key-1"
        jwk["use"] = "sig"
        jwk.setdefault("alg", "RS256")
        new_jwks = {"keys": [jwk]}
    if new_scopes is None and new_jwks is None:
        raise HTTPException(status_code=400, detail="must include scopes and/or jwk/public_key_pem")
    c = patch_client(client_id, scopes=new_scopes, jwks=new_jwks)
    if c is None:
        raise HTTPException(status_code=404, detail=f"client_id not registered: {client_id}")
    return JSONResponse({
        "client_id": c.client_id,
        "scopes": list(c.scopes),
        "jwks": c.jwks,
        "kids": [k.get("kid") for k in c.jwks.get("keys", []) if k.get("kid")],
    })


@router.put("/register-client/{client_id}", name="register_client_put",
            description="Full replace of an existing client registration.")
async def register_client_put(client_id: str, payload: dict, request: Request) -> JSONResponse:
    """RFC 7592-style PUT: replace the registration entirely."""
    _require_owner(request, client_id)
    payload = dict(payload)
    payload["client_id"] = client_id
    return await register_client(payload, _replace=True)


@router.delete("/register-client/{client_id}", name="register_client_delete",
               status_code=204,
               description="Remove an existing client registration.")
async def register_client_delete(client_id: str, request: Request):
    from app.auth.jwks import delete_client
    if not _CLIENT_ID_RE.match(client_id):
        raise HTTPException(status_code=400, detail="bad client_id")
    _require_owner(request, client_id)
    if not delete_client(client_id):
        raise HTTPException(status_code=404, detail=f"client_id not registered: {client_id}")
    from fastapi.responses import Response
    return Response(status_code=204)


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
