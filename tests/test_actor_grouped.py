"""EEHRxF Grouped Document Publisher + Access Provider actor.

verifies a round-trip: submit a bundle through ITI-105, then retrieve its
constituent resources through the access provider's queries.
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def test_round_trip_submission_then_access(client, auth_headers):
    submission_id = f"st-{uuid.uuid4().hex[:8]}"
    docref_id = f"dr-{submission_id}"
    bundle = {
        "resourceType": "Bundle",
        "id": submission_id,
        "type": "transaction",
        "entry": [
            {
                "resource": {
                    "resourceType": "DocumentReference",
                    "id": docref_id,
                    "status": "current",
                    "type": {"coding": [{"system": "http://loinc.org", "code": "11502-2"}]},
                    "category": [{"coding": [{"system":
                        "http://hl7.eu/fhir/ig/eu-health-data-api/CodeSystem/eehrxf-document-priority-category",
                        "code": "laboratory-report"}]}],
                    "subject": {"reference": "Patient/p-001"},
                    "content": [{"attachment": {"contentType": "application/fhir+json",
                                                "url": f"Binary/{submission_id}"}}],
                },
                "request": {"method": "POST", "url": "DocumentReference"},
            },
        ],
    }
    r = await client.post("/", headers=auth_headers, json=bundle)
    assert r.status_code == 201, r.text

    follow = await client.get(f"/DocumentReference/{docref_id}", headers=auth_headers)
    assert follow.status_code == 200
    fetched = follow.json()
    assert fetched["id"] == docref_id

    # the access provider's category-filtered search now returns the new ref too
    search = await client.get("/DocumentReference", headers=auth_headers,
                              params={"patient": "p-001", "category": "laboratory-report"})
    ids = [e["resource"]["id"] for e in search.json()["entry"]]
    assert docref_id in ids
