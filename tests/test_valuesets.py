"""compiled docs use the right LOINC document type + class category codes."""
from __future__ import annotations

import pytest

from app.fhir.ids import bundle_id, docref_id
from scripts.seed import DOC_TYPES, DOCREF_CATEGORY_CLASS, PANEL

pytestmark = pytest.mark.asyncio

# all seed DocumentReferences are produced by docref_id() now (UUIDs)
_SEED_DOCREF_IDS = {docref_id(p.pid, cat) for p in PANEL for cat in DOC_TYPES}


async def test_docrefs_use_loinc_document_class_category(client, auth_headers):
    """euridice-org PR #88: DocumentReference.category carries the coarse LOINC
    document-*class* code (document-classcodes), NOT the EHDS priority-category
    code — which is demoted off the wire. Patient Summary and ePrescription have
    no document-class mapping, so they carry no `.category` (identified by `.type`
    alone)."""
    r = await client.get("/DocumentReference", headers=auth_headers,
                         params={"patient": "p-001"})
    body = r.json()
    by_id = {e["resource"]["id"]: e["resource"] for e in body["entry"]}
    for category, cls in DOCREF_CATEGORY_CLASS.items():
        dr = by_id[docref_id("p-001", category)]
        if cls is None:
            assert "category" not in dr, f"{category} must carry no .category"
            continue
        codings = [c for cc in dr["category"] for c in cc["coding"]]
        codes = {c["code"] for c in codings}
        systems = {c["system"] for c in codings}
        assert cls["code"] in codes, f"{category}: expected class code {cls['code']}"
        assert "http://loinc.org" in systems
        # the retired priority-category CodeSystem must NOT appear on the wire
        assert not any("eehrxf-document-priority-category" in s for s in systems), \
            f"{category}: retired priority-category CodeSystem leaked onto the wire"


async def test_compiled_compositions_use_loinc_doctype(client, auth_headers):
    for category, doctype in DOC_TYPES.items():
        r = await client.get(f"/Bundle/{bundle_id('p-001', category)}", headers=auth_headers)
        assert r.status_code == 200, r.text
        bundle = r.json()
        comp = bundle["entry"][0]["resource"]
        coding = comp["type"]["coding"][0]
        assert coding["system"] == "http://loinc.org"
        assert coding["code"] == doctype["code"]
