"""EEHRxF Resource Consumer actor.

we don't actually call a peer FHIR server; instead we verify the shape of
queries an IPA-style consumer would emit, against our own resource access
provider. covers all IPA resource types + the EHDS medication-list ones.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

# (type, expected non-zero count for p-001)
IPA_SET = [
    "AllergyIntolerance", "Condition", "MedicationStatement", "MedicationRequest",
    "MedicationDispense", "Immunization", "Observation", "Procedure",
    "DiagnosticReport", "ImagingStudy", "Encounter",
]


@pytest.mark.parametrize("rtype", IPA_SET)
async def test_consumer_can_search_by_patient(client, auth_headers, rtype):
    r = await client.get(f"/{rtype}", headers=auth_headers, params={"patient": "Patient/p-001"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resourceType"] == "Bundle"
    assert body["total"] > 0, f"no {rtype} found for p-001"


@pytest.mark.parametrize("rtype", IPA_SET)
async def test_consumer_search_returns_fhir_searchset(client, auth_headers, rtype):
    r = await client.get(f"/{rtype}", headers=auth_headers, params={"patient": "p-001"})
    body = r.json()
    assert body["type"] == "searchset"
    for e in body["entry"]:
        assert "fullUrl" in e
        assert e["resource"]["resourceType"] == rtype


async def test_consumer_can_fetch_individual_resource(client, auth_headers):
    listing = (await client.get("/Observation", headers=auth_headers,
                                params={"patient": "p-001"})).json()
    first_id = listing["entry"][0]["resource"]["id"]
    r = await client.get(f"/Observation/{first_id}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["id"] == first_id
