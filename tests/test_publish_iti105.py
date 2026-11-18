"""ITI-105 Simplified Publish: POST /DocumentReference with the document inline."""
from __future__ import annotations

import base64
import hashlib
import json

import pytest

pytestmark = pytest.mark.asyncio

FOREIGN_SYSTEM = "urn:oid:1.2.3.4.5"


def _doc_bundle(*, patient_ident: tuple[str, str] = (FOREIGN_SYSTEM, "X-123"), family: str = "Visitor") -> dict:
    pat_url = "urn:uuid:11111111-1111-4111-8111-111111111111"
    return {
        "resourceType": "Bundle",
        "id": "my-doc-1",
        "identifier": {"system": "urn:ietf:rfc:3986", "value": "urn:uuid:22222222-2222-4222-8222-222222222222"},
        "type": "document",
        "timestamp": "2026-11-16T10:00:00Z",
        "entry": [
            {"fullUrl": "urn:uuid:33333333-3333-4333-8333-333333333333",
             "resource": {"resourceType": "Composition", "id": "c1", "status": "final",
                          "type": {"coding": [{"system": "http://loinc.org", "code": "60591-5"}]},
                          "subject": {"reference": pat_url}, "date": "2026-11-16T10:00:00Z",
                          "author": [{"display": "Dr Test"}], "title": "Patient Summary",
                          "section": [{"title": "Allergies", "code": {"coding": [{"system": "http://loinc.org", "code": "48765-2"}]},
                                       "entry": [{"reference": "urn:uuid:44444444-4444-4444-8444-444444444444"}]}]}},
            {"fullUrl": pat_url,
             "resource": {"resourceType": "Patient", "id": "pat-ext",
                          "identifier": [{"system": patient_ident[0], "value": patient_ident[1]}],
                          "name": [{"family": family, "given": ["Vera"]}], "birthDate": "1980-01-01",
                          "address": [{"country": "BE"}]}},
            {"fullUrl": "urn:uuid:44444444-4444-4444-8444-444444444444",
             "resource": {"resourceType": "AllergyIntolerance", "id": "a1",
                          "code": {"text": "Penicillin"}, "patient": {"reference": pat_url}}},
        ],
    }


def _docref(raw: bytes, **att) -> dict:
    return {
        "resourceType": "DocumentReference",
        "status": "current",
        "type": {"coding": [{"system": "http://loinc.org", "code": "60591-5"}]},
        "subject": {"reference": "Patient/not-on-this-server"},
        "content": [{"attachment": {"contentType": "application/fhir+json",
                                    "data": base64.b64encode(raw).decode(), **att}}],
    }


def _bytes(bundle: dict) -> bytes:
    # deliberately unusual formatting, so a re-serialisation would change the bytes
    return json.dumps(bundle, indent=3, ensure_ascii=False).encode() + b"\n\n"


async def test_publish_round_trip(client, auth_headers):
    raw = _bytes(_doc_bundle())
    r = await client.post("/DocumentReference", headers={**auth_headers, "Content-Type": "application/fhir+json"},
                          json=_docref(raw))
    assert r.status_code == 201, r.text
    loc = r.headers["location"]
    assert loc.endswith(f"/DocumentReference/{r.json()['id']}")
    dr = r.json()
    att = dr["content"][0]["attachment"]
    assert "data" not in att
    assert att["size"] == len(raw)
    assert att["hash"] == base64.b64encode(hashlib.sha1(raw).digest()).decode()  # noqa: S324
    assert att["url"] == f"Bundle/{dr['id']}"

    # the patient was created from the bundle and is findable by its identifier
    pid = dr["subject"]["reference"].split("/")[1]
    p = await client.get(f"/Patient/{pid}", headers=auth_headers)
    assert p.status_code == 200
    assert p.json()["name"][0]["family"] == "Visitor"

    # ITI-67 finds it, both ways
    s = await client.get(f"/DocumentReference?patient={pid}", headers=auth_headers)
    assert dr["id"] in [e["resource"]["id"] for e in s.json().get("entry", [])]
    s = await client.get(f"/DocumentReference?patient.identifier={FOREIGN_SYSTEM}|X-123", headers=auth_headers)
    assert dr["id"] in [e["resource"]["id"] for e in s.json().get("entry", [])]

    # ITI-68 returns the exact bytes
    g = await client.get(f"/{att['url']}", headers=auth_headers)
    assert g.status_code == 200
    assert g.content == raw
    assert g.headers["content-type"].startswith("application/fhir+json")

    # the allergy inside the document is searchable on its own
    a = await client.get(f"/AllergyIntolerance?patient={pid}", headers=auth_headers)
    assert a.json().get("total", 0) == 1


async def test_publish_links_existing_patient_by_identifier(client, auth_headers, pid):
    raw = _bytes(_doc_bundle(patient_ident=("urn:ehds-demo:slot", "p-001"), family="Müller"))
    r = await client.post("/DocumentReference", headers=auth_headers, json=_docref(raw))
    assert r.status_code == 201, r.text
    assert r.json()["subject"]["reference"] == f"Patient/{pid}"


async def test_publish_checks_client_hash_and_size(client, auth_headers):
    raw = _bytes(_doc_bundle())
    r = await client.post("/DocumentReference", headers=auth_headers, json=_docref(raw, size=len(raw) + 1))
    assert r.status_code == 400
    r = await client.post("/DocumentReference", headers=auth_headers, json=_docref(raw, hash="AAAA"))
    assert r.status_code == 400
    ok_hash = base64.b64encode(hashlib.sha1(raw).digest()).decode()  # noqa: S324
    r = await client.post("/DocumentReference", headers=auth_headers, json=_docref(raw, size=len(raw), hash=ok_hash))
    assert r.status_code == 201


async def test_publish_rejects_missing_data_and_non_documents(client, auth_headers):
    dr = _docref(b"x")
    dr["content"][0]["attachment"].pop("data")
    assert (await client.post("/DocumentReference", headers=auth_headers, json=dr)).status_code == 400
    assert (await client.post("/DocumentReference", headers=auth_headers,
                              json=_docref(b"not json"))).status_code == 400
    not_doc = dict(_doc_bundle(), type="collection")
    assert (await client.post("/DocumentReference", headers=auth_headers,
                              json=_docref(_bytes(not_doc)))).status_code == 400


async def test_publish_needs_the_create_scope(client, make_assertion):
    tok = await client.post("/token", data={
        "grant_type": "client_credentials",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": make_assertion(), "scope": "system/*.read"})
    hdr = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    r = await client.post("/DocumentReference", headers=hdr, json=_docref(_bytes(_doc_bundle())))
    assert r.status_code == 403


async def test_publish_plain_text_for_a_known_patient(client, auth_headers, pid):
    """ITB dap-015: non-FHIR content is accepted and findable, served at Binary/<id>."""
    dr = {"resourceType": "DocumentReference", "status": "current", "subject": {"reference": f"Patient/{pid}"},
          "content": [{"attachment": {"contentType": "text/plain", "data": "T1RCIHN1Ym1pc3Npb24gcHJvYmU="}}]}
    r = await client.post("/DocumentReference", headers=auth_headers, json=dr)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["subject"]["reference"] == f"Patient/{pid}"
    assert out["content"][0]["attachment"]["url"] == f"Binary/{out['id']}"
    s = await client.get(f"/DocumentReference?_id={out['id']}", headers=auth_headers)
    assert s.json()["total"] == 1
    raw = await client.get(f"/Binary/{out['id']}", headers={**auth_headers, "Accept": "text/plain"})
    assert raw.status_code == 200 and raw.content == b"OTB submission probe"
    js = await client.get(f"/Binary/{out['id']}", headers={**auth_headers, "Accept": "application/json"})
    assert js.json()["resourceType"] == "Binary"
    # unknown subject + no Bundle -> 422
    dr["subject"] = {"reference": "Patient/nobody-here"}
    assert (await client.post("/DocumentReference", headers=auth_headers, json=dr)).status_code == 422


async def test_bundle_upload_stores_binary_and_rewrites_attachment(client, auth_headers, pid):
    """ITB dap-010: a Binary uploaded in a Bundle is readable, by local or original id."""
    bundle = {"resourceType": "Bundle", "type": "transaction", "entry": [
        {"fullUrl": "urn:uuid:aaaaaaaa-0000-4000-8000-000000000001",
         "resource": {"resourceType": "Binary", "id": "my-binary-1", "contentType": "text/plain", "data": "aGVsbG8="},
         "request": {"method": "POST", "url": "Binary"}},
        {"fullUrl": "urn:uuid:aaaaaaaa-0000-4000-8000-000000000002",
         "resource": {"resourceType": "DocumentReference", "status": "current", "subject": {"reference": f"Patient/{pid}"},
                      "content": [{"attachment": {"contentType": "text/plain",
                                                  "url": "urn:uuid:aaaaaaaa-0000-4000-8000-000000000001"}}]},
         "request": {"method": "POST", "url": "DocumentReference"}}]}
    r = await client.post("/", headers=auth_headers, json=bundle)
    assert r.status_code == 201, r.text
    locs = [e["response"]["location"] for e in r.json()["entry"]]
    bin_loc = next(x for x in locs if x.startswith("Binary/"))
    dr_loc = next(x for x in locs if x.startswith("DocumentReference/"))
    dr = (await client.get(f"/{dr_loc}", headers=auth_headers)).json()
    assert dr["content"][0]["attachment"]["url"] == bin_loc
    for path in (f"/{bin_loc}", "/Binary/my-binary-1"):
        g = await client.get(path, headers={**auth_headers, "Accept": "text/plain"})
        assert g.status_code == 200 and g.content == b"hello", path


async def test_a_bare_bundle_sent_to_publish_says_how_to_wrap_it(client, auth_headers):
    # a client that sends the document itself (not wrapped in a DocumentReference)
    # must be told what to send instead
    r = await client.post("/DocumentReference", headers={**auth_headers, "Content-Type": "application/fhir+json"},
                          content=_bytes(_doc_bundle()))
    assert r.status_code == 400
    diag = r.json()["issue"][0]["diagnostics"]
    assert "Bundle" in diag and "content[0].attachment.data" in diag and "base64" in diag and "/llms.txt" in diag


async def test_a_document_bundle_posted_to_root_is_published(client, auth_headers):
    # POST / with a Bundle.type=document must not store loose resources only:
    # it publishes, so the document is found by ITI-67 and retrieved by ITI-68
    raw = _bytes(_doc_bundle(patient_ident=(FOREIGN_SYSTEM, "ROOT-1")))
    r = await client.post("/", headers={**auth_headers, "Content-Type": "application/fhir+json"}, content=raw)
    assert r.status_code == 201, r.text
    dr = r.json()
    assert dr["resourceType"] == "DocumentReference"
    assert r.headers["location"].endswith(f"/DocumentReference/{dr['id']}")
    pid = dr["subject"]["reference"].split("/", 1)[1]
    found = (await client.get(f"/DocumentReference?patient={pid}", headers=auth_headers)).json()
    assert dr["id"] in [e["resource"]["id"] for e in found.get("entry", [])]
    got = await client.get(f"/{dr['content'][0]['attachment']['url']}", headers=auth_headers)
    assert got.status_code == 200 and got.content == raw   # the exact bytes sent


async def test_a_transaction_bundle_posted_to_root_still_stores_resources(client, auth_headers):
    b = _doc_bundle(patient_ident=(FOREIGN_SYSTEM, "ROOT-2"))
    b["type"] = "transaction"
    for e in b["entry"]:
        e["request"] = {"method": "POST", "url": e["resource"]["resourceType"]}
    r = await client.post("/", headers=auth_headers, json=b)
    assert r.status_code == 201, r.text
    assert r.json()["type"] == "transaction-response"
