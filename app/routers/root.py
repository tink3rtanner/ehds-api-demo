"""``GET /``, ``/llms.txt`` and ``/quickstart.py``: the one URL you give an agent.

A browser asking for HTML is sent to the UI; anything else gets a discovery
document. ``/llms.txt`` is the same content as prose: a fresh agent should get
from it alone through register -> token -> find patient -> find documents ->
retrieve -> publish. Every URL in both is live (``app.fhir.examples``).

The source of truth for the API is the HL7 Europe Health Data API IG (CI build):
https://build.fhir.org/ig/euridice-org/eu-health-data-api/
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response

from app.config import settings
from app.fhir.examples import live_examples
from app.fhir.origin import (
    DERIVED_FROM_SYSTEM,
    EPIC_SANDBOX_CODE,
    ORIGIN_TAG_SYSTEM,
    SUBMISSION_TAG_SYSTEM,
)

router = APIRouter()

NAME = "EU Health Data API: demo server"
SPEC_URL = "https://build.fhir.org/ig/euridice-org/eu-health-data-api/"
DESCRIPTION = (
    "A FHIR R4 server for the HL7 Europe Health Data API (EHDS). It implements SMART Backend Services, "
    "IHE PDQm patient lookup, IHE MHD document search (ITI-67), retrieve (ITI-68) and publish "
    "(ITI-105 Simplified Publish), and RESTful resource access. Synthetic data only. "
    "Anyone can register a client, then read and publish with a token."
)
QUICKSTART = Path(__file__).resolve().parent.parent.parent / "examples" / "quickstart.py"

READ_SCOPE = "system/*.rs"
PUBLISH_SCOPE = "system/DocumentReference.c"


def _wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


def _publish_example(patient_ref: str) -> dict:
    return {
        "resourceType": "DocumentReference",
        "status": "current",
        "type": {"coding": [{"system": "http://loinc.org", "code": "60591-5", "display": "Patient summary Document"}]},
        "subject": {"reference": patient_ref},
        "content": [{"attachment": {"contentType": "application/fhir+json",
                                    "data": "<base64 of the document Bundle's bytes>"}}],
    }


def discovery_document() -> dict:
    ex = live_examples()
    base = ex["base_url"]
    ep = ex["endpoints"]
    pat = ex.get("patient") or {}
    pid = pat.get("id", "<patient-id>")
    open_urls = [f"{base}/", f"{base}/llms.txt", ep["capability_statement"], ep["smart_configuration"],
                 ep["jwks"], ep["register_client"], ep["token"], f"{base}/quickstart.py"]
    steps = [
        {
            "step": 1, "name": "register",
            "standard": "OAuth 2.0 Dynamic Client Registration (RFC 7591), open to anyone",
            "request": {"method": "POST", "url": ep["register_client"], "content_type": "application/json",
                        "body": {"client_id": "my-agent-1",
                                 "client_name": "<who you are, e.g. 'Claude agent for Jane Doe, Acme Health'>",
                                 "scopes": [READ_SCOPE, PUBLISH_SCOPE],
                                 "jwk": "<your public key as a JWK (RSA or EC)>  OR  public_key_pem: '<PEM>'"}},
            "notes": "GET the same URL for the full schema. The key id to put in your JWT header is "
                     "returned as next_steps.client_assertion_kid (<client_id>-key-1).",
        },
        {
            "step": 2, "name": "token",
            "standard": "SMART App Launch 2 Backend Services (client_credentials + private_key_jwt)",
            "request": {"method": "POST", "url": ep["token"], "content_type": "application/x-www-form-urlencoded",
                        "form": {"grant_type": "client_credentials",
                                 "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                                 "client_assertion": "<JWT signed with your private key>",
                                 "scope": f"{READ_SCOPE} {PUBLISH_SCOPE}"}},
            "client_assertion": {"header": {"alg": "RS256 (or RS384, ES256, ES384)", "kid": "<client_id>-key-1"},
                                 "claims": {"iss": "<client_id>", "sub": "<client_id>", "aud": ep["token"],
                                            "exp": "now + 60..300 s", "jti": "<random, single use>"}},
            "notes": f"Send Authorization: Bearer <access_token> on every FHIR request. "
                     f"Tokens last {settings.token_ttl_seconds // 60} minutes.",
        },
        {
            "step": 3, "name": "find-patient",
            "standard": "IHE PDQm ITI-78",
            "request": {"method": "GET", "url": ep.get("lookup_patient_by_slot")},
            "also": [ep.get("search_patient_by_demographics"), f"POST {base}/Patient/$match"],
        },
        {
            "step": 4, "name": "find-documents",
            "standard": "IHE MHD ITI-67",
            "request": {"method": "GET", "url": f"{base}/DocumentReference?patient={pid}"},
            "notes": "Filter by type (LOINC), e.g. &type=http://loinc.org|60591-5. "
                     "patient.identifier=<system>|<value> also works.",
        },
        {
            "step": 5, "name": "retrieve",
            "standard": "IHE MHD ITI-68",
            "request": {"method": "GET", "url": ep.get("document_retrieve")},
            "notes": "GET the DocumentReference's content[0].attachment.url (relative to the base URL). "
                     "You get a FHIR document Bundle (Bundle.type=document).",
        },
        {
            "step": 6, "name": "publish",
            "standard": "IHE MHD ITI-105 Simplified Publish",
            "request": {"method": "POST", "url": f"{base}/DocumentReference", "content_type": "application/fhir+json",
                        "scope": PUBLISH_SCOPE, "body": _publish_example(f"Patient/{pid}")},
            "notes": ("Put the document Bundle (type=document, Composition first, Patient included) base64-encoded "
                      "in content[0].attachment.data. subject may name a Patient that is not on this server: the "
                      "server resolves it from the Patient inside the Bundle (matching an existing Patient by "
                      "identifier, else creating it). hash and size are optional; the server computes them and "
                      "rejects a mismatch. Response: 201, Location: /DocumentReference/<id>, body = the stored "
                      "DocumentReference. Its attachment.url returns your exact bytes."),
        },
    ]
    return {
        "name": NAME,
        "description": DESCRIPTION,
        "specification": SPEC_URL,
        "fhir_base_url": base,
        "fhir_version": "4.0.1",
        "synthetic_data_only": True,
        "access": {
            "summary": "Anyone can register a client, then read and publish with a token.",
            "open_without_token": open_urls,
            "everything_else": "needs Authorization: Bearer <token>",
            "read_scope": READ_SCOPE,
            "publish_scope": PUBLISH_SCOPE,
            "scope_syntax": "SMART v2 (.cruds) and v1 (.read/.write) are both accepted.",
        },
        "start_here": {
            "llms_txt": f"{base}/llms.txt",
            "capability_statement": ep["capability_statement"],
            "smart_configuration": ep["smart_configuration"],
            "register_client": ep["register_client"],
            "quickstart_script": f"{base}/quickstart.py",
            "human_ui": f"{base}/ui/" if not settings.is_prod else None,
        },
        "steps": steps,
        "priority_categories": ex.get("priority_categories", {}),
        "reference_patient": pat or None,
        "match_parameters": ex.get("match_parameters"),
        "examples": ep,
        "tags": {
            ORIGIN_TAG_SYSTEM: "reference = seeded by this server; community = published by a client",
            SUBMISSION_TAG_SYSTEM: "the id of the publish a resource arrived in",
            DERIVED_FROM_SYSTEM: f"code {EPIC_SANDBOX_CODE}: data derived from the public fhir.epic.com sandbox. "
                                 "Add it to DocumentReference.meta.tag when you publish; it is kept and labelled.",
        },
        "identity_notes": {
            "patient_ids_are_uuids": True,
            "slot_identifier_system": ex["slot_identifier_system"],
            "published_resources_get_local_ids": (
                "Resources inside a published Bundle are stored under local ids. Your original id is kept as "
                "identifier urn:ehds-demo:source-id|<your id>. GET /{Type}/{id}/$source links back."),
        },
        "conveniences_not_in_the_ig": {
            "upload_bundle": f"POST {base}/ with a transaction or document Bundle (scope system/Bundle.write). "
                             "Stores the resources; does not create a DocumentReference. Use ITI-105 instead.",
            "patient_summary_operation": ep.get("patient_summary_operation"),
        },
    }


def llms_text() -> str:
    d = discovery_document()
    base = d["fhir_base_url"]
    acc = d["access"]
    s = {x["name"]: x for x in d["steps"]}
    pat = d.get("reference_patient") or {}
    pid = pat.get("id", "<patient-id>")
    lines = [
        f"# {d['name']}",
        "",
        d["description"],
        "",
        f"- Base URL: {base} (FHIR {d['fhir_version']}, JSON)",
        f"- Specification: {d['specification']}",
        f"- CapabilityStatement: {d['start_here']['capability_statement']}",
        f"- SMART configuration: {d['start_here']['smart_configuration']}",
        f"- Python quickstart (read-only: keypair, register, token, a few reads, prints the token for Bruno/Postman): "
        f"{d['start_here']['quickstart_script']}",
        "",
        "## Access",
        f"{acc['summary']} These need no token: " + ", ".join(acc["open_without_token"]) + ".",
        f"Everything else needs a bearer. Read scope: {acc['read_scope']}. Publish scope: {acc['publish_scope']}. "
        f"{acc['scope_syntax']}",
        "",
        "## 1. Register a client",
        f"Make an RSA (2048+) or EC P-256 keypair. POST JSON to {s['register']['request']['url']}:",
        '  {"client_id": "my-agent-1", "client_name": "Claude agent for Jane Doe, Acme Health", '
        '"scopes": ["system/*.rs", "system/DocumentReference.c"], "public_key_pem": "-----BEGIN PUBLIC KEY-----\\n..."}',
        "Please set client_name to who you are (a person, organisation, or the agent and who it works for). "
        "It is shown in the server's audit log so the demo's owner can tell clients apart.",
        "(or \"jwk\": {...} instead of public_key_pem). client_id: 3-64 chars, lowercase letters, digits, - and _, "
        "starting and ending with a letter or digit. It must be new (409 if taken). The response is 200 with "
        "next_steps.client_assertion_kid, the kid to use: <client_id>-key-1. To change scopes or rotate the key later, "
        "PATCH /register-client/<client_id> with a bearer token issued to that client.",
        "",
        "## 2. Get a token",
        "Sign a JWT with your private key. Header: alg RS256 (or RS384/ES256/ES384), kid <client_id>-key-1. "
        f"Claims: iss = sub = <client_id>, aud = {s['token']['request']['url']}, exp = now + 60 s, jti = a random string.",
        f"POST {s['token']['request']['url']} as application/x-www-form-urlencoded:",
        "  grant_type=client_credentials",
        "  client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "  client_assertion=<the JWT>",
        "  scope=system/*.rs system/DocumentReference.c",
        f"The response has access_token. {s['token']['notes']}",
        "",
        "## 3. Find a patient (IHE PDQm ITI-78)",
        f"GET {s['find-patient']['request']['url']}   (URL-encode the | as %7C if your HTTP library does not)",
        f"GET {s['find-patient']['also'][0]}",
        f"POST {base}/Patient/$match with a Parameters resource (resource = a Patient with name, birthDate, gender).",
        f"Patient ids are uuids. The 10 seeded patients also carry a slot identifier "
        f"{d['identity_notes']['slot_identifier_system']}|p-001 ... p-010.",
        "",
        "## 4. Find documents (IHE MHD ITI-67)",
        f"GET {base}/DocumentReference?patient={pid}",
        f"GET {base}/DocumentReference?patient={pid}&type=http://loinc.org|60591-5",
        f"GET {base}/DocumentReference?patient.identifier={d['identity_notes']['slot_identifier_system']}|p-001",
        "Also supported: _id, identifier, category, status, date, creation, period, _lastUpdated, format, "
        "security-label, related, setting, facility, event, author.given, author.family.",
        "",
        "## 5. Retrieve a document (IHE MHD ITI-68)",
        "GET the DocumentReference's content[0].attachment.url. It is relative to the base URL, e.g.",
        f"GET {s['retrieve']['request']['url']}",
        "You get a FHIR document Bundle (Bundle.type=document, Composition first). Seeded documents are "
        "compiled when you ask; published documents come back byte for byte as they were sent.",
        "",
        "## 6. Publish a document (IHE MHD ITI-105 Simplified Publish)",
        f"POST {base}/DocumentReference with Content-Type: application/fhir+json and a token holding "
        f"{acc['publish_scope']}. Body:",
        "  " + json.dumps(_publish_example("Patient/<id>")),
        "- data is the base64 of the document Bundle's bytes. The Bundle must have type=document, a Composition "
        "as its first entry, and the Patient the Composition is about.",
        "- subject can name a Patient that is not on this server. The server uses the Patient inside the Bundle: "
        "if one of its identifiers matches a Patient here, the document is filed under that Patient; otherwise "
        "the Patient is created.",
        "- hash (base64 SHA-1) and size are optional. The server computes them and rejects a mismatch.",
        "- type defaults to the Composition.type when you leave it out.",
        f"- Response: 201 Created, Location: {base}/DocumentReference/<id>, body = the stored DocumentReference "
        "(no data; attachment.url = Bundle/<id>, with size and hash).",
        "- Read subject.reference from that body: it is the Patient the document was filed under. A Patient created "
        "from your Bundle gets a new local id (your id is kept as identifier urn:ehds-demo:source-id|<id>).",
        "- Your document then shows up in GET /DocumentReference?patient=<that Patient id>, and GET Bundle/<id> "
        "returns exactly the bytes you sent.",
        "- Non-FHIR content (contentType text/plain, application/pdf, ...) is accepted too, if subject is a Patient "
        "this server holds. Its attachment.url is Binary/<id>, which returns the raw bytes (or the FHIR Binary "
        "resource if you send Accept: application/fhir+json).",
        "- After the 201 the server checks a FHIR document against the HL7 Europe profile for its type. The result "
        f"is informational and never rejects a publish. Read it at {base}/ui/api/validation/<id> "
        "(state pending, validated, failed or unavailable).",
        f"- Data from the public fhir.epic.com sandbox: add meta.tag {{\"system\": \"{DERIVED_FROM_SYSTEM}\", "
        f"\"code\": \"{EPIC_SANDBOX_CODE}\"}} to the DocumentReference. It is kept, and the UI labels the document "
        "\"Derived from the public fhir.epic.com sandbox\".",
        "",
        "## The six EHDS priority categories",
        "Four are documents (found with ITI-67 by LOINC type); ePrescription and eDispensation are resources "
        "(read through the FHIR API).",
    ]
    for key, c in (d.get("priority_categories") or {}).items():
        if c["kind"] == "document":
            also = f"; stored type also carries {', '.join(c['also'])}" if c.get("also") else ""
            lines.append(f"- {c['name']} ({key}, LOINC {c['loinc']}{also}): GET {c['query']}")
            lines.append(f"  retrieve: GET {c['retrieve']}  profile: {c['profile']}")
        else:
            lines.append(f"- {c['name']} ({c['resourceType']}): GET {c['query']}  profile: {c['profile']}")
    lines += [
        "",
        "## Reading single resources",
        "GET /{Type}/{id} and GET /{Type}?patient=<id> for Patient, AllergyIntolerance, Condition, "
        "MedicationStatement, MedicationRequest, MedicationDispense, Medication, Immunization, Observation, "
        "Procedure, DiagnosticReport, ImagingStudy, Encounter, Specimen, Practitioner, Organization, Composition, "
        "DocumentReference. ?patient also accepts patient.identifier=<system>|<value>.",
        "",
        "## Tags on stored resources (meta.tag)",
    ]
    lines += [f"- {k}: {v}" for k, v in d["tags"].items()]
    lines += [
        "",
        "## Identity",
        f"- {d['identity_notes']['published_resources_get_local_ids']}",
        "",
        "## Errors",
        "- 401: no token, or the token is expired or not from this server. Get a new token.",
        "- 403: the token lacks the scope; the message names the scope. Re-register (or PATCH "
        f"{base}/register-client/<client_id>) with that scope, then get a new token.",
        "- 400 / 422 on publish: an OperationOutcome says what is wrong with the DocumentReference or Bundle.",
        "",
        "## Not part of the IG (conveniences)",
        f"- {d['conveniences_not_in_the_ig']['upload_bundle']}",
        f"- IPS $summary: GET {d['conveniences_not_in_the_ig']['patient_summary_operation']}",
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


@router.get("/quickstart.py", include_in_schema=False)
async def quickstart() -> PlainTextResponse:
    text = QUICKSTART.read_text().replace("https://ehds.joshpriebe.com", settings.base_url)
    return PlainTextResponse(text, media_type="text/x-python; charset=utf-8")
