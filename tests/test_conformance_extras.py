"""Smaller conformance fixes from docs/resource-identity.md §7 + the EU-profile
audit: searchset `self` link (Task C) and the prescription-Bundle profile bug.
"""
from __future__ import annotations

import pytest

from app.fhir.document import compile_document
from app.fhir.ids import patient_id

# ---- Task C: searchset carries a `self` link ----

@pytest.mark.asyncio
async def test_searchset_has_self_link(client, auth_headers):
    r = await client.get("/Observation?_count=1", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "searchset"
    links = {link["relation"]: link["url"] for link in body.get("link", [])}
    assert "self" in links
    assert links["self"].endswith("/Observation?_count=1")
    # match entries still carry search.mode
    if body.get("entry"):
        assert body["entry"][0]["search"]["mode"] == "match"


@pytest.mark.asyncio
async def test_patient_searchset_has_self_link(client, auth_headers):
    r = await client.get("/Patient", headers=auth_headers)
    assert r.status_code == 200
    links = {link["relation"]: link["url"] for link in r.json().get("link", [])}
    assert links.get("self", "").endswith("/Patient")


# ---- prescription Bundle must NOT claim a resource profile ----

def test_prescription_bundle_has_no_resource_profile():
    """`MedicationRequest-eu-mpd` is a resource profile; stamping it on a Bundle
    makes the validator reject every Bundle.* element. The prescription document
    Bundle must carry no `meta.profile` at all."""
    b = compile_document(patient_id("p-001"), "prescription")
    profiles = (b.get("meta") or {}).get("profile", [])
    assert profiles == [] or "meta" not in b
    assert not any("eu-mpd" in p for p in profiles)


def test_eu_document_categories_keep_their_bundle_profile():
    """The real document categories still stamp their EU Bundle profile."""
    for category, marker in [
        ("patient-summary", "bundle-eu-eps"),
        ("laboratory-report", "Bundle-eu-lab"),
        ("discharge-report", "bundle-eu-hdr"),
        ("imaging-report", "BundleReportEuImaging"),
    ]:
        b = compile_document(patient_id("p-001"), category)
        profiles = (b.get("meta") or {}).get("profile", [])
        assert any(marker in p for p in profiles), f"{category} lost its bundle profile"
