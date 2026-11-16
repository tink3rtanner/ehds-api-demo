"""Slot ownership everywhere, and FHIR-typed errors from the security layer.

An upload can store a copy of a reference Patient that keeps its
urn:ehds-demo:slot identifier. Only the Patient whose id derives from the slot
owns it; every slot lookup must ignore copies. (The compiled-document index
already does; see test_publish_real_documents.py.)
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.fhir.ids import bundle_id, patient_id
from app.security import BodySizeLimitMiddleware, RateLimitMiddleware

pytestmark = pytest.mark.asyncio


async def _upload_copy_of_p001(client, auth_headers) -> str:
    """POST / a p-001 document; return the id of the Patient copy it stores."""
    raw = (await client.get(f"/Bundle/{bundle_id('p-001', 'patient-summary')}", headers=auth_headers)).content
    r = await client.post("/", headers={**auth_headers, "Content-Type": "application/fhir+json"}, content=raw)
    assert r.status_code == 201, r.text[:300]
    copies = [e["response"]["location"].split("/")[1] for e in r.json()["entry"]
              if e["response"]["location"].startswith("Patient/")]
    assert copies and copies[0] != patient_id("p-001")
    return copies[0]


async def test_spec_bundle_id_by_slot_names_the_seeded_patient(client, auth_headers):
    await _upload_copy_of_p001(client, auth_headers)
    r = await client.get("/spec/bundle-id/p-001/patient-summary", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["patient"] == patient_id("p-001")


async def test_spec_bundle_id_for_a_copy_has_no_documents(client, auth_headers):
    copy_id = await _upload_copy_of_p001(client, auth_headers)
    r = await client.get(f"/spec/bundle-id/{copy_id}/patient-summary", headers=auth_headers)
    assert r.status_code == 404


async def test_every_advertised_bundle_resolves_and_each_slot_appears_once(client, auth_headers):
    await _upload_copy_of_p001(client, auth_headers)
    rows = (await client.get("/spec/all-bundle-ids", headers=auth_headers)).json()["bundles"]
    seen = [(b["slot"], b["category"]) for b in rows]
    assert len(seen) == len(set(seen)), "a slot is listed more than once"
    for b in rows:
        assert b["patient"] == patient_id(b["slot"]), b
        g = await client.get(b["path"], headers=auth_headers)
        assert g.status_code != 404, f"{b['path']} is advertised but 404s"


# ---------- security-layer errors are FHIR ----------

def _tiny_app(middleware, **kw):
    app = Starlette(routes=[Route("/x", lambda r: PlainTextResponse("ok"), methods=["GET", "POST"])])
    app.add_middleware(middleware, **kw)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_body_too_large_is_a_fhir_operation_outcome():
    async with _tiny_app(BodySizeLimitMiddleware, max_bytes=10) as c:
        r = await c.post("/x", content=b"x" * 100)
    assert r.status_code == 413
    assert r.headers["content-type"].startswith("application/fhir+json")
    assert r.json()["resourceType"] == "OperationOutcome"


async def test_rate_limited_is_a_fhir_operation_outcome():
    async with _tiny_app(RateLimitMiddleware, per_minute=1) as c:
        await c.get("/x")
        r = await c.get("/x")
    assert r.status_code == 429
    assert r.headers["content-type"].startswith("application/fhir+json")
    assert r.headers["retry-after"] == "30"


async def test_patient_search_by_slot_resolves_to_the_reference_patient(client, auth_headers, pid):
    """A bare copy carrying urn:ehds-demo:slot|p-001 must not capture ?patient=p-001."""
    copy = {"resourceType": "Bundle", "type": "transaction", "entry": [{
        "fullUrl": "urn:uuid:bbbbbbbb-0000-4000-8000-000000000001",
        "resource": {"resourceType": "Patient", "id": "impostor",
                     "identifier": [{"system": "urn:ehds-demo:slot", "value": "p-001"}]},
        "request": {"method": "POST", "url": "Patient"}}]}
    r = await client.post("/", headers=auth_headers, json=copy)
    assert r.status_code == 201, r.text[:300]
    obs = (await client.get("/Observation?patient=p-001", headers=auth_headers)).json()
    subjects = {e["resource"]["subject"]["reference"] for e in obs.get("entry", [])}
    assert subjects == {f"Patient/{pid}"}
    spec = (await client.get("/spec/bundle-id/p-001/patient-summary", headers=auth_headers)).json()
    assert spec["patient"] == pid
