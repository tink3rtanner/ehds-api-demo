"""``GET /`` and ``/llms.txt`` — the one URL you point an agent at.

The base URL is what goes on the slide. A browser asking for HTML is sent to
the UI; anything else (agents, curl) gets a discovery document that points at
``metadata`` and ``smart-configuration`` (the standard-defined sources of
truth) and adds what those cannot express: the four exchange pillars with one
live example request each, the priority categories with their profile
canonicals, and how to publish. ``/llms.txt`` is the same content as prose for
agents that prefer reading to parsing.

``POST /`` remains the ITI-105 submission endpoint (app/routers/docsubmit.py).
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response

from app.config import settings
from app.fhir.examples import live_examples

router = APIRouter()

NAME = "EU Health Data API — reference implementation"
DESCRIPTION = (
    "FHIR R4 server implementing the HL7 Europe Health Data API exchange layer: "
    "SMART Backend Services authorisation, IHE PDQm patient lookup, IHE MHD "
    "document search and retrieve, IPA resource access, and ITI-105 publishing. "
    "Synthetic data only. Anyone can read, register a client and submit example documents."
)


def _wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept


def discovery_document() -> dict:
    ex = live_examples()
    base = ex["base_url"]
    ep = ex["endpoints"]
    docs = ex.get("documents", {})
    pillars = [
        {
            "pillar": "authorize",
            "standard": "SMART App Launch — Backend Services (client_credentials + private_key_jwt)",
            "what": "A client signs a JWT assertion with its registered key and receives a short-lived bearer.",
            "register_first": ep["register_client"],
            "token_endpoint": ep["token"],
            "example": {"method": "POST", "url": ep["token"],
                        "form": {"grant_type": "client_credentials",
                                 "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                                 "client_assertion": "<signed JWT, aud = token endpoint>",
                                 "scope": "system/*.read"}},
        },
        {
            "pillar": "find-patient",
            "standard": "IHE PDQm (ITI-78) — Patient search and $match",
            "what": "Search for a patient by demographics, or POST $match for scored candidates.",
            "example": {"method": "GET", "url": ep.get("search_patient_by_demographics")},
            "match": {"method": "POST", "url": f"{base}/Patient/$match", "body": ex.get("match_parameters")},
        },
        {
            "pillar": "find-documents",
            "standard": "IHE MHD (ITI-67) — DocumentReference search",
            "what": "List the documents registered for a patient, filterable by type and category.",
            "example": {"method": "GET", "url": ep.get("document_search_by_identifier")},
        },
        {
            "pillar": "retrieve-document",
            "standard": "IHE MHD (ITI-68) — retrieve the document Bundle",
            "what": "Fetch one document as a Bundle.type=document that follows the HL7 Europe profile for its category.",
            "example": {"method": "GET", "url": ep.get("document_retrieve")},
        },
        {
            "pillar": "resource-access",
            "standard": "HL7 IPA — RESTful read and search on the patient compartment",
            "what": "Read or search individual resources instead of whole documents.",
            "example": {"method": "GET", "url": ep.get("allergies_for_patient")},
        },
        {
            "pillar": "publish",
            "standard": "IHE MHD (ITI-105) — Simplified Publish",
            "what": "POST a transaction or document Bundle to the base URL with scope system/Bundle.write. "
                    "The server accepts it, then validates it against the EU profile; a failed validation does not reject it.",
            "example": {"method": "POST", "url": f"{base}/",
                        "content_type": "application/fhir+json", "body": "<Bundle>"},
        },
    ]
    return {
        "name": NAME,
        "description": DESCRIPTION,
        "fhir_base_url": base,
        "fhir_version": "4.0.1",
        "synthetic_data_only": True,
        "start_here": {
            "capability_statement": ep["capability_statement"],
            "smart_configuration": ep["smart_configuration"],
            "register_client": ep["register_client"],
            "llms_txt": f"{base}/llms.txt",
            "human_ui": f"{base}/ui/" if not settings.is_prod else None,
        },
        "pillars": pillars,
        "priority_categories": docs,
        "reference_patient": ex.get("patient"),
        "examples": ep,
        "identity_notes": {
            "patient_ids_are_uuids": True,
            "slot_identifier_system": ex["slot_identifier_system"],
            "resolve_slot": ep.get("lookup_patient_by_slot"),
            "submitted_resources_are_re_identified": (
                "ITI-105 submissions are naturalised to local ids; follow entry.response.location "
                "or search identifier=urn:ehds-demo:source-id|<your id>. GET /{Type}/{id}/$source links back."
            ),
        },
    }


def llms_text() -> str:
    d = discovery_document()
    ep = d["examples"]
    lines = [
        f"# {d['name']}",
        "",
        d["description"],
        "",
        f"Base URL: {d['fhir_base_url']}  (FHIR {d['fhir_version']}, synthetic data only)",
        "",
        "## Start here",
        f"- CapabilityStatement: {d['start_here']['capability_statement']}",
        f"- SMART configuration: {d['start_here']['smart_configuration']}",
        f"- Register a client (GET describes the schema, POST registers): {d['start_here']['register_client']}",
        "",
        "## How to get a token",
        "1. Generate an RSA or EC keypair. POST {client_id, scopes, public_key_pem|jwk} to the registration endpoint.",
        f"2. Sign a JWT: iss = sub = client_id, aud = {ep['token']}, exp = now+60s, jti = random; header kid = <client_id>-key-1.",
        f"3. POST {ep['token']} as form data: grant_type=client_credentials, "
        "client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer, client_assertion=<jwt>, scope=system/*.read",
        "4. Send Authorization: Bearer <access_token> on every FHIR request. Tokens live 15 minutes.",
        "",
        "## The four exchange pillars (one live example each)",
    ]
    for p in d["pillars"]:
        ex = p.get("example") or {}
        lines.append(f"- {p['pillar']} — {p['standard']}")
        lines.append(f"  {p['what']}")
        if ex.get("url"):
            lines.append(f"  {ex.get('method', 'GET')} {ex['url']}")
    lines += ["", "## Priority-category documents (Bundle.type=document)"]
    for slug, info in (d.get("priority_categories") or {}).items():
        prof = info.get("profile") or "base R4 document (no EU profile in R4)"
        lines.append(f"- {slug}: LOINC {info['loinc']} · {info['url']} · profile {prof}")
    lines += [
        "",
        "## Identity",
        "- Patient ids are uuids. The reference panel's slot labels (p-001 … p-010) are Patient.identifier values "
        f"with system {d['identity_notes']['slot_identifier_system']}; resolve with {d['identity_notes']['resolve_slot']}",
        f"- {d['identity_notes']['submitted_resources_are_re_identified']}",
        "",
        "## Publishing",
        f"POST a Bundle (type transaction or document) to {d['fhir_base_url']}/ with Content-Type: application/fhir+json "
        "and a bearer holding system/Bundle.write. You get 201 and a transaction-response with local ids. "
        "The server then validates the bundle against the HL7 Europe profile for its category and shows the result at "
        f"{d['fhir_base_url']}/ui/#/coverage. A failed validation does not reject the submission.",
        "",
    ]
    return "\n".join(lines)


@router.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def root(request: Request) -> Response:
    if _wants_html(request) and not settings.is_prod:
        return RedirectResponse(url="/ui/", status_code=307)
    return JSONResponse(discovery_document())


@router.get("/llms.txt", include_in_schema=False)
async def llms() -> PlainTextResponse:
    return PlainTextResponse(llms_text(), media_type="text/plain; charset=utf-8")
