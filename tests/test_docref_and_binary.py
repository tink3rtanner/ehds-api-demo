"""ITI-67 DocumentReference search + ITI-68 Binary on-demand compile."""
from __future__ import annotations

import pytest

from app.fhir.capability import PROFILE_EU_BUNDLE
from scripts.seed import PANEL, DOC_TYPES

pytestmark = pytest.mark.asyncio

CATEGORIES = list(DOC_TYPES.keys())


async def test_docref_per_patient_per_category(client, auth_headers):
    for p in PANEL:
        r = await client.get("/DocumentReference", headers=auth_headers,
                             params={"patient": p.pid})
        assert r.status_code == 200, r.text
        body = r.json()
        seed_entries = [e for e in body["entry"] if e["resource"]["id"].startswith("dr-p-")]
        assert len(seed_entries) == len(CATEGORIES), f"{p.pid}: {len(seed_entries)}"
        cats_seen = set()
        for e in seed_entries:
            for cc in e["resource"]["category"]:
                for c in cc["coding"]:
                    cats_seen.add(c["code"])
        assert set(CATEGORIES) <= cats_seen


async def test_docref_filter_by_category(client, auth_headers):
    r = await client.get("/DocumentReference", headers=auth_headers,
                         params={"patient": "p-001", "category": "laboratory-report"})
    assert r.status_code == 200
    body = r.json()
    seed_entries = [e for e in body["entry"] if e["resource"]["id"].startswith("dr-p-")]
    assert len(seed_entries) == 1


@pytest.mark.parametrize("category", CATEGORIES)
async def test_compile_document_for_all_patients(client, auth_headers, category):
    for p in PANEL:
        bin_id = f"doc-{p.pid}-{category}"
        r = await client.get(f"/Binary/{bin_id}", headers=auth_headers)
        assert r.status_code == 200, f"{bin_id}: {r.status_code} {r.text[:300]}"
        bundle = r.json()
        assert bundle["resourceType"] == "Bundle"
        assert bundle["type"] == "document"
        assert bundle["entry"][0]["resource"]["resourceType"] == "Composition"
        assert bundle["meta"]["profile"] == [PROFILE_EU_BUNDLE[category]]
        # patient is somewhere in the bundle
        types = {e["resource"]["resourceType"] for e in bundle["entry"]}
        assert "Patient" in types


async def test_binary_unknown_returns_404(client, auth_headers):
    r = await client.get("/Binary/doc-p-999-patient-summary", headers=auth_headers)
    assert r.status_code == 404


async def test_binary_unknown_category_returns_404(client, auth_headers):
    r = await client.get("/Binary/doc-p-001-not-a-category", headers=auth_headers)
    assert r.status_code == 404
