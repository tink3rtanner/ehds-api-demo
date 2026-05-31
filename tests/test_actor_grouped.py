"""EEHRxF Grouped Document Publisher + Access Provider actor.

verifies a round-trip: submit a bundle through ITI-105, then retrieve its
constituent resources through the access provider's queries.
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def test_round_trip_submission_then_access(client, auth_headers, pid):
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
                    "subject": {"reference": f"Patient/{pid}"},
                    "content": [{"attachment": {"contentType": "application/fhir+json",
                                                "url": f"Binary/{submission_id}"}}],
                },
                "request": {"method": "POST", "url": "DocumentReference"},
            },
        ],
    }
    r = await client.post("/", headers=auth_headers, json=bundle)
    assert r.status_code == 201, r.text

    # the submission is naturalized to a LOCAL id; follow the returned location.
    loc = r.json()["entry"][0]["response"]["location"]
    assert loc.startswith("DocumentReference/")
    local_docref_id = loc.split("/", 1)[1]
    assert local_docref_id != docref_id

    follow = await client.get(f"/{loc}", headers=auth_headers)
    assert follow.status_code == 200
    fetched = follow.json()
    assert fetched["id"] == local_docref_id
    # subject ref to the (out-of-bundle) seeded patient is preserved, not rewritten
    assert fetched["subject"]["reference"] == f"Patient/{pid}"
    # original submission id preserved as a source identifier
    assert {"system": "urn:ehds-demo:source-id", "value": docref_id} in fetched["identifier"]

    # the access provider's category-filtered search now returns the new ref too
    search = await client.get("/DocumentReference", headers=auth_headers,
                              params={"patient": pid, "category": "laboratory-report"})
    ids = [e["resource"]["id"] for e in search.json()["entry"]]
    assert local_docref_id in ids
