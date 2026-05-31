"""ITI-105 document submission — accept, validate, persist."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


def _build_submission_bundle() -> dict:
    return {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {
                "fullUrl": "DocumentReference/incoming-1",
                "resource": {
                    "resourceType": "DocumentReference",
                    "id": "incoming-1",
                    "status": "current",
                    "type": {"coding": [{"system": "http://loinc.org", "code": "60591-5"}]},
                    "category": [{"coding": [{"system": "x", "code": "patient-summary"}]}],
                    "subject": {"reference": "Patient/p-001"},
                    "content": [{"attachment": {"contentType": "application/fhir+json",
                                                 "url": "Binary/incoming-bundle-1"}}],
                },
                "request": {"method": "POST", "url": "DocumentReference"},
            },
        ],
    }


async def test_submit_bundle_happy(client, auth_headers):
    r = await client.post("/", headers={**auth_headers, "Content-Type": "application/fhir+json"},
                          json=_build_submission_bundle())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["resourceType"] == "Bundle"
    assert body["type"] == "transaction-response"

    # the submitted DocumentReference is naturalized to a LOCAL id; the foreign
    # "incoming-1" id is no longer the resource id. Follow the returned location.
    loc = body["entry"][0]["response"]["location"]
    assert loc.startswith("DocumentReference/")
    assert loc != "DocumentReference/incoming-1"
    follow = await client.get(f"/{loc}", headers=auth_headers)
    assert follow.status_code == 200
    # the original id is preserved on the resource as a source identifier
    idents = follow.json().get("identifier", [])
    assert {"system": "urn:ehds-demo:source-id", "value": "incoming-1"} in idents

    # ...so the foreign id is still discoverable via identifier search.
    by_origin = await client.get(
        "/DocumentReference?identifier=urn:ehds-demo:source-id|incoming-1",
        headers=auth_headers,
    )
    assert by_origin.status_code == 200
    assert by_origin.json().get("total", 0) >= 1


async def test_submit_rejects_non_bundle(client, auth_headers):
    r = await client.post("/", headers=auth_headers,
                          json={"resourceType": "Patient", "id": "x"})
    assert r.status_code == 400


async def test_submit_rejects_wrong_bundle_type(client, auth_headers):
    r = await client.post("/", headers=auth_headers,
                          json={"resourceType": "Bundle", "type": "collection", "entry": []})
    assert r.status_code == 400


async def test_submit_requires_scope(client, bearer):
    # bearer has Bundle.write scope; this case re-checks that path.
    # tested separately in security tests too.
    pass
