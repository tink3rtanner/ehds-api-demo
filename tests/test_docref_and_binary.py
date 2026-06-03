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
    # one DocumentReference per priority category per patient. `.type` carries
    # the per-category LOINC document type; `.category` carries the LOINC
    # document-class code where one is defined (PR #88).
    type_codes = {t["code"] for t in DOC_TYPES.values()}
    for p in PANEL:
        r = await client.get("/DocumentReference", headers=auth_headers,
                             params={"patient": p.pid})
        assert r.status_code == 200, r.text
        body = r.json()
        seed_entries = [e for e in body["entry"] if e["resource"]["id"] in SEED_DOCREF_IDS]
        assert len(seed_entries) == len(CATEGORIES), f"{p.pid}: {len(seed_entries)}"
        types_seen = {c["code"]
                      for e in seed_entries
                      for c in e["resource"]["type"]["coding"]}
        assert type_codes <= types_seen


async def test_docref_filter_by_category(client, auth_headers):
    # category search filters on the LOINC document-class code now (PR #88);
    # 26436-6 is the Laboratory Studies (set) class. `patient=` accepts the slot.
    r = await client.get("/DocumentReference", headers=auth_headers,
                         params={"patient": "p-001", "category": "26436-6"})
    assert r.status_code == 200
    body = r.json()
    seed_entries = [e for e in body["entry"] if e["resource"]["id"] in SEED_DOCREF_IDS]
    assert len(seed_entries) == 1
    assert seed_entries[0]["resource"]["id"] == docref_id("p-001", "laboratory-report")


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
        # prescription has no EU document-Bundle profile (PROFILE_EU_BUNDLE is
        # None) — it must carry no meta.profile; the rest stamp theirs.
        if PROFILE_EU_BUNDLE[category] is None:
            assert "profile" not in (bundle.get("meta") or {})
        else:
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
