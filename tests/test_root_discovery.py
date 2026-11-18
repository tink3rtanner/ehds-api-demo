"""GET / discovery document, /llms.txt, and the single source of example URLs."""
from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.asyncio

_SLOT_AS_ID = re.compile(r"/(Patient|Bundle|Observation|DocumentReference)/p-\d{3}\b")


async def test_root_json_for_non_browser_callers(client):
    r = await client.get("/", headers={"Accept": "application/json"})
    assert r.status_code == 200
    d = r.json()
    assert d["fhir_base_url"] == "http://testserver"
    assert d["fhir_version"] == "4.0.1"
    assert d["synthetic_data_only"] is True
    assert [s["name"] for s in d["steps"]] == [
        "register", "token", "find-patient", "find-documents", "retrieve", "publish"]
    assert d["steps"][-1]["request"]["url"] == "http://testserver/DocumentReference"
    assert d["access"]["publish_scope"] == "system/DocumentReference.c"
    assert "Anyone can read" not in str(d)
    assert d["start_here"]["capability_statement"].endswith("/metadata")
    assert d["start_here"]["smart_configuration"].endswith("/.well-known/smart-configuration")
    assert d["start_here"]["llms_txt"].endswith("/llms.txt")
    assert set(d["priority_categories"]) == {
        "patient-summary", "laboratory-report", "discharge-report", "imaging-report",
        "eprescription", "edispensation"}


async def test_root_default_accept_is_json_too(client):
    """curl and most HTTP libraries send */* — that is an agent, not a browser."""
    r = await client.get("/", headers={"Accept": "*/*"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")


async def test_root_html_goes_to_ui_in_dev(client):
    r = await client.get("/", headers={"Accept": "text/html,application/xhtml+xml"}, follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/ui/"


async def test_root_head_is_allowed(client):
    """link checkers and uptime probes HEAD the base URL"""
    r = await client.head("/", headers={"Accept": "application/json"})
    assert r.status_code == 200
    r = await client.head("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 307


async def test_root_post_is_the_bundle_convenience_upload(client, auth_headers):
    r = await client.post("/", headers=auth_headers, json={"resourceType": "Patient"})
    assert r.status_code == 400  # rejected as "expected Bundle" by the submit handler, not a 405


async def test_llms_txt(client):
    r = await client.get("/llms.txt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    body = r.text
    for needle in ("http://testserver/token", "http://testserver/metadata", "register", "Bearer",
                   "patient-summary", "system/DocumentReference.c", "POST http://testserver/DocumentReference",
                   "urn:ehds-demo:derived-from", "fhir.epic.com-sandbox", "ePrescription", "eDispensation"):
        assert needle in body, needle
    assert not _SLOT_AS_ID.search(body)


async def test_every_example_url_resolves(client, auth_headers):
    """Design goal 5: no example may point at something that does not exist."""
    d = (await client.get("/", headers={"Accept": "application/json"})).json()
    ex = d["examples"]
    assert not any(_SLOT_AS_ID.search(v) for v in ex.values()), ex
    for key in ("read_patient", "eprescription_for_patient", "edispensation_for_patient",
                "observations_for_patient", "allergies_for_patient",
                "document_search", "document_search_by_identifier", "document_retrieve",
                "lookup_patient_by_slot", "search_patient_by_demographics", "patient_summary_operation"):
        url = ex[key]
        r = await client.get(url, headers=auth_headers)
        assert r.status_code == 200, f"{key}: {url} -> {r.status_code}"
    # searches must actually find the reference patient, not return empty sets
    for key in ("lookup_patient_by_slot", "search_patient_by_demographics", "document_search",
                "eprescription_for_patient", "edispensation_for_patient"):
        r = await client.get(ex[key], headers=auth_headers)
        assert r.json()["total"] >= 1, key
    # every priority-category query finds something; document ones find exactly that document
    for key, c in d["priority_categories"].items():
        r = await client.get(c["query"], headers=auth_headers)
        assert r.status_code == 200 and r.json()["total"] >= 1, key
        if c["kind"] == "document":
            urls = [e["resource"]["content"][0]["attachment"]["url"] for e in r.json()["entry"]]
            assert any(c["retrieve"].endswith(u) for u in urls), key
            assert (await client.get(c["retrieve"], headers=auth_headers)).status_code == 200


async def test_metadata_and_discovery_need_no_token(client):
    for path in ("/", "/llms.txt", "/metadata", "/.well-known/smart-configuration", "/quickstart.py"):
        assert (await client.get(path, headers={"Accept": "application/json"})).status_code == 200, path
    r = await client.get("/Patient")
    assert r.status_code == 401 and r.json()["resourceType"] == "OperationOutcome"


async def test_match_example_body_matches_the_reference_patient(client, auth_headers):
    d = (await client.get("/", headers={"Accept": "application/json"})).json()
    params = d["match_parameters"]
    r = await client.post("/Patient/$match", headers={**auth_headers, "Content-Type": "application/fhir+json"},
                          json=params)
    assert r.status_code == 200, r.text
    assert r.json()["total"] >= 1
    assert r.json()["entry"][0]["resource"]["id"] == d["reference_patient"]["id"]


async def test_smart_configuration_shares_the_same_examples(client):
    d = (await client.get("/", headers={"Accept": "application/json"})).json()
    sc = (await client.get("/.well-known/smart-configuration")).json()
    assert sc["example_endpoints"] == d["examples"]
    assert sc["discovery_document"] == "http://testserver/"
    assert "/ui/#/implement" not in str(sc)


async def test_bruno_collection_downloads_without_keys(client):
    import io
    import zipfile
    r = await client.get("/bruno.zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert "bruno/bruno.json" in names and "bruno/collection.bru" in names
    assert sum(n.endswith(".bru") for n in names) > 60
    assert not [n for n in names if n.endswith(".pem")]   # a developer's local key never ships
    d = (await client.get("/", headers={"Accept": "application/json"})).json()
    assert d["start_here"]["bruno_collection"].endswith("/bruno.zip")
    assert "/bruno.zip" in (await client.get("/llms.txt")).text
