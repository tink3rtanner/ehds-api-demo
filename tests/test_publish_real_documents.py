"""Publish and retrieve REAL documents, not hand-built minimal ones.

The hand-built fixtures in test_publish_iti105.py missed a crash that every
document this server compiles triggers (R4 Composition.identifier is a single
object, not a list). These tests take the documents the server itself serves,
publish them back, and follow the attachment url to the bytes. They also pin
down that a failed publish leaves nothing behind and that unknown ids are a
plain 404 OperationOutcome.
"""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest

from app.config import settings
from app.fhir.document import CATEGORY_TO_DOC_TYPE
from app.fhir.ids import bundle_id
from app.fhir.origin import REFERENCE, origin_of

pytestmark = pytest.mark.asyncio

CATEGORIES = sorted(CATEGORY_TO_DOC_TYPE)


def _store_snapshot() -> set[Path]:
    """Every stored file except logs and validation records."""
    root = Path(settings.data_dir)
    skip = {"audit", "validation", "validator-home"}
    return {p for p in root.rglob("*") if p.is_file() and p.relative_to(root).parts[0] not in skip}


async def _real_document(client, auth_headers, category: str) -> bytes:
    """The bytes of a document of this category the server lists, found the way
    a client would: ITI-67 search by type, then ITI-68 on the attachment url.
    Only seeded (reference) FHIR documents: other tests publish into the same
    store, and their submissions must not change what this helper returns."""
    code = CATEGORY_TO_DOC_TYPE[category]["code"]
    s = await client.get(f"/DocumentReference?type=http://loinc.org|{code}", headers=auth_headers)
    assert s.status_code == 200, s.text[:300]
    urls = sorted(
        e["resource"]["content"][0]["attachment"]["url"] for e in s.json().get("entry", [])
        if origin_of(e["resource"])["kind"] == REFERENCE
        and e["resource"]["content"][0]["attachment"].get("contentType", "").startswith("application/fhir+json"))
    assert urls, f"the seeded panel lists no {category} document"
    r = await client.get(f"/{urls[0]}", headers=auth_headers)
    assert r.status_code == 200, r.text[:300]
    return r.content


def _docref(raw: bytes, subject: str) -> dict:
    return {"resourceType": "DocumentReference", "status": "current",
            "subject": {"reference": subject},
            "content": [{"attachment": {"contentType": "application/fhir+json",
                                        "data": base64.b64encode(raw).decode()}}]}


@pytest.mark.parametrize("category", CATEGORIES)
async def test_publish_round_trips_every_document_this_server_compiles(client, auth_headers, pid, category):
    raw = await _real_document(client, auth_headers, category)
    r = await client.post("/DocumentReference", headers=auth_headers, json=_docref(raw, f"Patient/{pid}"))
    assert r.status_code == 201, r.text[:500]
    att = r.json()["content"][0]["attachment"]
    assert att["size"] == len(raw)
    assert att["hash"] == base64.b64encode(hashlib.sha1(raw).digest()).decode()  # noqa: S324

    # ITI-68: follow the url the server handed out, get the exact bytes back
    g = await client.get(f"/{att['url']}", headers=auth_headers)
    assert g.status_code == 200
    assert g.content == raw


@pytest.mark.parametrize("category", CATEGORIES)
async def test_bundle_upload_accepts_every_document_this_server_compiles(client, auth_headers, category):
    raw = await _real_document(client, auth_headers, category)
    r = await client.post("/", headers={**auth_headers, "Content-Type": "application/fhir+json"}, content=raw)
    assert r.status_code == 201, r.text[:500]


async def test_a_publish_that_fails_part_way_writes_nothing(client, auth_headers, pid, monkeypatch):
    raw = await _real_document(client, auth_headers, "patient-summary")
    before = _store_snapshot()

    import app.routers.publish as publish

    def boom(*_a, **_k):
        raise RuntimeError("injected failure after the document was parsed")
    monkeypatch.setattr(publish, "_drop_dangling", boom)

    with pytest.raises(RuntimeError, match="injected"):
        await client.post("/DocumentReference", headers=auth_headers, json=_docref(raw, f"Patient/{pid}"))
    assert _store_snapshot() - before == set()


async def test_a_non_fhir_publish_that_fails_part_way_writes_nothing(client, auth_headers, pid, monkeypatch):
    before = _store_snapshot()

    import app.routers.publish as publish

    def boom(*_a, **_k):
        raise RuntimeError("injected failure after the Binary was built")
    monkeypatch.setattr(publish, "_drop_dangling", boom)

    doc = _docref(b"plain text note", f"Patient/{pid}")
    doc["content"][0]["attachment"]["contentType"] = "text/plain"
    with pytest.raises(RuntimeError, match="injected"):
        await client.post("/DocumentReference", headers=auth_headers, json=doc)
    assert _store_snapshot() - before == set()


async def test_a_rejected_publish_writes_nothing(client, auth_headers):
    raw = b'{"resourceType":"Bundle","type":"document","entry":[]}'
    before = _store_snapshot()
    r = await client.post("/DocumentReference", headers=auth_headers, json=_docref(raw, "Patient/not-here"))
    assert 400 <= r.status_code < 500
    assert _store_snapshot() - before == set()


@pytest.mark.parametrize("path", ["/Binary/otb-no-such-binary", "/Bundle/otb-no-such-bundle",
                                  "/DocumentReference/otb-no-such-docref"])
async def test_unknown_ids_are_a_plain_404_operation_outcome(client, auth_headers, path):
    r = await client.get(path, headers={**auth_headers, "Accept": "application/fhir+json"}, follow_redirects=False)
    assert r.status_code == 404, f"{path}: {r.status_code} {r.headers.get('location', '')}"
    assert r.headers["content-type"].startswith("application/fhir+json")
    assert r.json()["resourceType"] == "OperationOutcome"


async def test_a_legacy_binary_alias_still_redirects_to_its_document(client, auth_headers):
    bid = bundle_id("p-001", "patient-summary")
    r = await client.get(f"/Binary/{bid}", headers=auth_headers, follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"].endswith(f"/Bundle/{bid}")


async def test_an_unknown_binary_still_needs_a_bearer(client):
    r = await client.get("/Binary/otb-no-such-binary", follow_redirects=False)
    assert r.status_code == 401


def test_every_router_error_is_typed_as_fhir():
    """A JSONResponse with a 4xx/5xx status in the routers, main or security must say
    application/fhir+json. Catches the next hand-built error that forgets."""
    import re
    root = Path(__file__).resolve().parent.parent / "app"
    bad = []
    for f in [*sorted((root / "routers").glob("*.py")), root / "main.py", root / "security.py"]:
        src = f.read_text()
        for m in re.finditer(r"JSONResponse\((?:[^()]|\([^()]*\))*?status_code=[45]\d\d(?:[^()]|\([^()]*\))*\)", src):
            call = m.group(0)
            if "media_type" not in call and '{"error"' not in call:  # OAuth errors stay OAuth-shaped
                bad.append(f"{f.name}:{src[:m.start()].count(chr(10)) + 1}")
    assert bad == []


async def test_uploading_a_reference_document_does_not_hijack_its_patient(client, auth_headers, pid):
    """A Bundle upload creates a community copy of the Patient, slot identifier
    and all. The compiled documents must still resolve to the seeded Patient."""
    bid = bundle_id("p-001", "patient-summary")
    raw = (await client.get(f"/Bundle/{bid}", headers=auth_headers)).content
    up = await client.post("/", headers={**auth_headers, "Content-Type": "application/fhir+json"}, content=raw)
    assert up.status_code == 201, up.text[:300]

    r = await client.get(f"/Bundle/{bid}", headers=auth_headers)
    assert r.status_code == 200, r.text[:300]
    comp = next(e["resource"] for e in r.json()["entry"] if e["resource"]["resourceType"] == "Composition")
    assert comp["subject"]["reference"].endswith(pid)
