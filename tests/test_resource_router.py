"""generic resource router — read + patient-compartment search."""
from __future__ import annotations

import pytest

from app.fhir.origin import REFERENCE, origin_of

pytestmark = pytest.mark.asyncio

# (type, expected count for p-001)
EXPECTED_BY_PATIENT = [
    ("AllergyIntolerance", 3),
    ("Condition", 5),
    ("MedicationStatement", 5),
    ("MedicationRequest", 5),
    ("MedicationDispense", 3),
    ("Immunization", 4),
    ("Procedure", 3),
    ("Observation", 10),
    ("DiagnosticReport", 3),  # 2 lab + 1 rad
    ("ImagingStudy", 1),
    ("Encounter", 2),
    ("Specimen", 3),
]


@pytest.mark.parametrize("rtype, expected", EXPECTED_BY_PATIENT)
async def test_patient_compartment_counts(client, auth_headers, rtype, expected):
    # the SEEDED compartment: other tests publish p-001's documents back, which
    # (rightly) links their resources to p-001 too, tagged community
    r = await client.get(f"/{rtype}", headers=auth_headers, params={"patient": "p-001", "_count": "1000"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resourceType"] == "Bundle"
    seeded = [e for e in body.get("entry", []) if origin_of(e["resource"])["kind"] == REFERENCE]
    assert len(seeded) == expected, f"{rtype}: expected {expected} seeded, got {len(seeded)}"


async def test_read_observation(client, auth_headers, child_id_for):
    r = await client.get(f"/Observation/{child_id_for('p-001', 'Observation', 0)}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["resourceType"] == "Observation"


async def test_filter_observation_by_category(client, auth_headers):
    r = await client.get("/Observation", headers=auth_headers,
                         params={"patient": "p-001", "category": "vital-signs"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] > 0
    for e in body["entry"]:
        cats = [c["code"] for cc in e["resource"]["category"] for c in cc["coding"]]
        assert "vital-signs" in cats


async def test_filter_medication_request_by_status(client, auth_headers):
    r = await client.get("/MedicationRequest", headers=auth_headers,
                         params={"patient": "p-001", "status": "active"})
    assert r.status_code == 200
    assert r.json()["total"] == 5
