"""ITI-67 DocumentReference search + ITI-68 Binary on-demand compile."""
from __future__ import annotations

import pytest

from app.fhir.capability import PROFILE_EU_BUNDLE
from app.fhir.ids import bundle_id, docref_id
from scripts.seed import DOC_TYPES, PANEL

pytestmark = pytest.mark.asyncio

CATEGORIES = list(DOC_TYPES.keys())
SEED_DOCREF_IDS = {docref_id(p.pid, c) for p in PANEL for c in CATEGORIES}


async def test_docref_per_patient_per_category(client, auth_headers):
    for p in PANEL:
        r = await client.get("/DocumentReference", headers=auth_headers,
                             params={"patient": p.pid})
        assert r.status_code == 200, r.text
        body = r.json()
        seed_entries = [e for e in body["entry"] if e["resource"]["id"] in SEED_DOCREF_IDS]
        assert len(seed_entries) == len(CATEGORIES), f"{p.pid}: {len(seed_entries)}"
        cats_seen = set()
        for e in seed_entries:
            for cc in e["resource"]["category"]:
                for c in cc["coding"]:
                    cats_seen.add(c["code"])
        assert set(CATEGORIES) <= cats_seen


async def test_docref_filter_by_category(client, auth_headers):
    # the `patient=` search param accepts either uuid or the slot identifier
    r = await client.get("/DocumentReference", headers=auth_headers,
                         params={"patient": "p-001", "category": "laboratory-report"})
    assert r.status_code == 200
    body = r.json()
    seed_entries = [e for e in body["entry"] if e["resource"]["id"] in SEED_DOCREF_IDS]
    assert len(seed_entries) == 1


@pytest.mark.parametrize("category", CATEGORIES)
async def test_compile_document_for_all_patients(client, auth_headers, category):
    for p in PANEL:
        bid = bundle_id(p.pid, category)
        r = await client.get(f"/Bundle/{bid}", headers=auth_headers)
        assert r.status_code == 200, f"/Bundle/{bid}: {r.status_code} {r.text[:300]}"
        bundle = r.json()
        assert bundle["resourceType"] == "Bundle"
        assert bundle["type"] == "document"
        assert bundle["entry"][0]["resource"]["resourceType"] == "Composition"
        assert bundle["meta"]["profile"] == [PROFILE_EU_BUNDLE[category]]
        # patient is somewhere in the bundle
        types = {e["resource"]["resourceType"] for e in bundle["entry"]}
        assert "Patient" in types


async def test_bundle_unknown_returns_404(client, auth_headers):
    r = await client.get("/Bundle/00000000-0000-0000-0000-000000000000", headers=auth_headers)
    assert r.status_code == 404


async def test_patient_summary_operation(client, auth_headers, pid):
    """IPS $summary returns the same bundle as /Bundle/{patient-summary-uuid}."""
    r = await client.get(f"/Patient/{pid}/$summary", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["resourceType"] == "Bundle"
    assert body["type"] == "document"
    assert body["id"] == bundle_id("p-001", "patient-summary")
