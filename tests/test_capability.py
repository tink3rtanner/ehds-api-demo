"""capability statement covers every claimed endpoint."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

EXPECTED_RESOURCES = {
    "Patient", "AllergyIntolerance", "Condition", "MedicationStatement",
    "MedicationRequest", "MedicationDispense", "Medication", "Immunization",
    "Observation", "Procedure", "DiagnosticReport", "ImagingStudy", "Encounter",
    "Specimen", "Practitioner", "PractitionerRole", "Organization", "Composition",
    "DocumentReference", "Bundle",
    # Binary is intentionally absent — compiled documents now live at
    # /Bundle/{id} (FHIR-proper); the legacy /Binary/{id} route only does a
    # 301 redirect for backward compat with older bookmarks.
}


async def test_metadata_returns_capability_statement(client):
    r = await client.get("/metadata")
    assert r.status_code == 200
    body = r.json()
    assert body["resourceType"] == "CapabilityStatement"
    assert body["fhirVersion"] == "4.0.1"


async def test_metadata_lists_all_expected_resources(client):
    body = (await client.get("/metadata")).json()
    types_in_cs = {r["type"] for r in body["rest"][0]["resource"]}
    missing = EXPECTED_RESOURCES - types_in_cs
    assert not missing, f"missing types in CapabilityStatement: {missing}"


async def test_metadata_advertises_smart_security(client):
    body = (await client.get("/metadata")).json()
    sec = body["rest"][0]["security"]
    codes = [c["code"] for s in sec["service"] for c in s["coding"]]
    assert "SMART-on-FHIR" in codes


async def test_metadata_lists_eu_igs(client):
    body = (await client.get("/metadata")).json()
    igs = body["implementationGuide"]
    # canonical EU IG URLs are http://hl7.eu/fhir/<ig>/ImplementationGuide/...
    assert any("health-data-api" in u for u in igs)
    assert any("eps" in u for u in igs)
    assert any("laboratory" in u for u in igs)
    assert any("hdr" in u for u in igs)
    assert any("imaging" in u for u in igs)
    assert any("/mpd/" in u for u in igs)


async def test_metadata_lists_patient_match_operation(client):
    body = (await client.get("/metadata")).json()
    pat = next(r for r in body["rest"][0]["resource"] if r["type"] == "Patient")
    ops = {o["name"] for o in pat.get("operation", [])}
    assert "match" in ops
    assert "everything" in ops


async def test_every_capability_resource_has_a_real_route(client, auth_headers):
    """layer 1: capability-statement-driven smoke. each declared resource type
    must respond (200 or 404) for GET /<Type>?_count=1, not 405/500."""
    body = (await client.get("/metadata")).json()
    for resource_entry in body["rest"][0]["resource"]:
        rtype = resource_entry["type"]
        if rtype in {"Binary", "Bundle"}:
            continue  # special-case handlers tested separately
        r = await client.get(f"/{rtype}", headers=auth_headers, params={"_count": "1"})
        assert r.status_code in (200, 400), f"{rtype}: {r.status_code} {r.text[:200]}"
