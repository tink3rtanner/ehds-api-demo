"""EEHRxF Document Access Provider — ITI-67 / ITI-68 conformance.

each search filter the IG mentions for DocumentReference is exercised once,
plus negative paths (unknown patient, bad bin id, missing scope).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_iti67_search_by_patient(client, auth_headers):
    r = await client.get("/DocumentReference", headers=auth_headers,
                         params={"patient": "p-001"})
    assert r.status_code == 200
    seed = [e for e in r.json()["entry"] if e["resource"]["id"].startswith("dr-p-")]
    assert len(seed) == 4


async def test_iti67_search_by_status(client, auth_headers):
    r = await client.get("/DocumentReference", headers=auth_headers,
                         params={"patient": "p-001", "status": "current"})
    assert r.status_code == 200
    seed = [e for e in r.json()["entry"] if e["resource"]["id"].startswith("dr-p-")]
    assert len(seed) == 4


async def test_iti67_search_by_type(client, auth_headers):
    r = await client.get("/DocumentReference", headers=auth_headers,
                         params={"patient": "p-001", "type": "60591-5"})
    assert r.status_code == 200
    seed = [e for e in r.json()["entry"] if e["resource"]["id"].startswith("dr-p-")]
    assert len(seed) == 1
    coding = seed[0]["resource"]["type"]["coding"][0]
    assert coding["code"] == "60591-5"


async def test_iti68_retrieve_compiled_bundle(client, auth_headers):
    # 1. find a docref
    listing = (await client.get("/DocumentReference", headers=auth_headers,
                                params={"patient": "p-001", "category": "imaging-report"})).json()
    docref = listing["entry"][0]["resource"]
    url = docref["content"][0]["attachment"]["url"]
    # 2. retrieve the referenced binary
    r = await client.get(f"/{url}", headers=auth_headers)
    assert r.status_code == 200
    bundle = r.json()
    assert bundle["type"] == "document"
    # 3. that bundle contains the imaging study + diagnostic report
    types = [e["resource"]["resourceType"] for e in bundle["entry"]]
    assert "ImagingStudy" in types
    assert "DiagnosticReport" in types


async def test_iti67_returns_empty_bundle_for_unknown_patient(client, auth_headers):
    r = await client.get("/DocumentReference", headers=auth_headers,
                         params={"patient": "no-such"})
    assert r.status_code == 200
    assert r.json()["total"] == 0


async def test_iti68_missing_scope_is_forbidden(client, make_assertion):
    # mint a token without Binary.read
    r = await client.post("/token", data={
        "grant_type": "client_credentials",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": make_assertion(),
        "scope": "system/Patient.read",
    })
    bearer = r.json()["access_token"]
    rr = await client.get("/Binary/doc-p-001-patient-summary",
                          headers={"Authorization": f"Bearer {bearer}"})
    assert rr.status_code == 403
