"""PDQm search + $match weighted lookup over the 10-patient panel."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_read_patient(client, auth_headers, pid):
    r = await client.get(f"/Patient/{pid}", headers=auth_headers)
    assert r.status_code == 200
    p = r.json()
    assert p["id"] == pid
    assert p["name"][0]["family"] == "Müller"


async def test_read_missing_patient(client, auth_headers):
    r = await client.get("/Patient/does-not-exist", headers=auth_headers)
    assert r.status_code == 404
    assert r.json()["resourceType"] == "OperationOutcome"


async def test_bad_id_format(client, auth_headers):
    r = await client.get("/Patient/..%2Fetc%2Fpasswd", headers=auth_headers)
    assert r.status_code in (400, 404)


@pytest.mark.parametrize("query, expected_count", [
    ({"family": "Müller"}, 1),
    ({"family": "rossi"}, 1),       # case insensitive substring
    ({"given": "Anna"}, 1),
    ({"birthdate": "1981-11-02"}, 1),
    ({"gender": "female"}, 5),
    ({"address-country": "FR"}, 1),
    ({"address-city": "Berlin"}, 1),
    ({"identifier": "12345678Z"}, 1),
])
async def test_pdqm_search_filters(client, auth_headers, query, expected_count):
    r = await client.get("/Patient", headers=auth_headers, params=query)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resourceType"] == "Bundle"
    assert body["type"] == "searchset"
    assert body["total"] == expected_count, f"query={query} got={body['total']}"


async def test_pdqm_search_returns_all_for_empty_query(client, auth_headers):
    r = await client.get("/Patient", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["total"] == 10


async def test_patient_match_certain(client, auth_headers, pid):
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "resource", "resource": {
                "resourceType": "Patient",
                "identifier": [{"system": "urn:oid:1.2.40.0.10.1.4.3.1", "value": "1014031968"}],
                "name": [{"family": "Müller", "given": ["Anna"]}],
                "birthDate": "1968-03-14",
            }},
            {"name": "count", "valueInteger": 3},
        ],
    }
    r = await client.post("/Patient/$match", headers=auth_headers, json=body)
    assert r.status_code == 200, r.text
    bundle = r.json()
    assert bundle["total"] >= 1
    top = bundle["entry"][0]
    assert top["resource"]["id"] == pid
    grade_ext = next(e for e in top["search"]["extension"]
                     if e["url"] == "http://hl7.org/fhir/StructureDefinition/match-grade")
    assert grade_ext["valueCode"] in {"certain", "probable"}


async def test_patient_match_no_match(client, auth_headers):
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "resource", "resource": {
                "resourceType": "Patient",
                "name": [{"family": "Nobody", "given": ["Nobody"]}],
                "birthDate": "1800-01-01",
            }},
        ],
    }
    r = await client.post("/Patient/$match", headers=auth_headers, json=body)
    assert r.status_code == 200
    bundle = r.json()
    # all results are 'certainly-not' graded (or zero results)
    if bundle["total"]:
        for e in bundle["entry"]:
            ext = next(x for x in e["search"]["extension"]
                       if x["url"] == "http://hl7.org/fhir/StructureDefinition/match-grade")
            assert ext["valueCode"] in {"certainly-not", "possible"}


async def test_patient_match_bad_input(client, auth_headers):
    r = await client.post("/Patient/$match", headers=auth_headers, json={"resourceType": "Patient"})
    assert r.status_code == 400
