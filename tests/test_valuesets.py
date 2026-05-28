"""compiled docs use the right LOINC + EHDS category codes."""
from __future__ import annotations

import pytest

from app.fhir.ids import bundle_id, docref_id
from scripts.seed import DOC_TYPES, PANEL

pytestmark = pytest.mark.asyncio

# all seed DocumentReferences are produced by docref_id() now (UUIDs)
_SEED_DOCREF_IDS = {docref_id(p.pid, cat) for p in PANEL for cat in DOC_TYPES}


async def test_docrefs_use_priority_category_codesystem(client, auth_headers):
    r = await client.get("/DocumentReference", headers=auth_headers,
                         params={"patient": "p-001"})
    body = r.json()
    cs = "http://hl7.eu/fhir/ig/eu-health-data-api/CodeSystem/eehrxf-document-priority-category"
    valid_codes = set(DOC_TYPES.keys())
    seed_entries = [e for e in body["entry"] if e["resource"]["id"] in _SEED_DOCREF_IDS]
    assert len(seed_entries) == len(DOC_TYPES), f"expected {len(DOC_TYPES)} seed DocumentReferences, got {len(seed_entries)}"
    for e in seed_entries:
        cats = e["resource"]["category"]
        coding = next((c for cc in cats for c in cc["coding"] if c["system"] == cs), None)
        assert coding is not None, f"{e['resource']['id']} missing priority-category coding"
        assert coding["code"] in valid_codes


async def test_compiled_compositions_use_loinc_doctype(client, auth_headers):
    for category, doctype in DOC_TYPES.items():
        r = await client.get(f"/Bundle/{bundle_id('p-001', category)}", headers=auth_headers)
        assert r.status_code == 200, r.text
        bundle = r.json()
        comp = bundle["entry"][0]["resource"]
        coding = comp["type"]["coding"][0]
        assert coding["system"] == "http://loinc.org"
        assert coding["code"] == doctype["code"]
