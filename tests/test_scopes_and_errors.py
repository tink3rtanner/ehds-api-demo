"""SMART v1/v2 scope matching, token rules and error shapes."""
from __future__ import annotations

import time

import pytest

from app.auth.scopes import covers, is_valid

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("granted,required,ok", [
    ("system/DocumentReference.c", "system/DocumentReference.c", True),
    ("system/DocumentReference.cruds", "system/DocumentReference.c", True),
    ("system/DocumentReference.write", "system/DocumentReference.c", True),
    ("system/*.write", "system/DocumentReference.c", True),
    ("system/*.rs", "system/DocumentReference.c", False),
    ("system/*.read", "system/DocumentReference.c", False),
    ("system/Bundle.write", "system/DocumentReference.c", False),
    ("system/*.rs", "system/Patient.read", True),
    ("system/*.read", "system/Observation.rs", True),
    ("system/Patient.r", "system/Patient.read", False),   # read = r + s
    ("system/Patient.rs", "system/Observation.read", False),
])
async def test_covers(granted, required, ok):
    assert covers(granted, required) is ok


async def test_scope_syntax():
    assert is_valid("system/*.rs") and is_valid("system/DocumentReference.c") and is_valid("system/Patient.read")
    assert not is_valid("system/Patient.sr")      # v2 letters are ordered
    assert not is_valid("patient/Patient.read")   # backend services only
    assert not is_valid("system/patient.read")


async def test_register_accepts_v2_scopes_and_rejects_garbage(client, test_client_jwk):
    pub = {k: v for k, v in test_client_jwk.items() if k in ("kty", "n", "e")}
    r = await client.post("/register-client", json={"client_id": "v2-scopes", "jwk": pub,
                                                    "scopes": ["system/*.rs", "system/DocumentReference.c"]})
    assert r.status_code == 200, r.text
    r = await client.post("/register-client", json={"client_id": "bad-scopes", "jwk": pub, "scopes": ["admin"]})
    assert r.status_code == 400
    assert r.json()["resourceType"] == "OperationOutcome"


async def test_token_rejects_long_lived_assertion(client, make_assertion):
    r = await client.post("/token", data={
        "grant_type": "client_credentials",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": make_assertion(exp=int(time.time()) + 3600), "scope": "system/*.read"})
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_client"   # OAuth shape, top level


async def test_token_errors_are_oauth_shaped(client):
    r = await client.post("/token", data={"grant_type": "password"})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_request"


async def test_fhir_errors_are_operation_outcomes(client, auth_headers):
    r = await client.get("/Patient")
    assert r.status_code == 401 and r.json()["resourceType"] == "OperationOutcome"
    r = await client.get("/Patient/does-not-exist", headers=auth_headers)
    assert r.status_code == 404 and r.json()["resourceType"] == "OperationOutcome"


async def test_smart_configuration_matches_backend_services(client):
    sc = (await client.get("/.well-known/smart-configuration")).json()
    assert sc["grant_types_supported"] == ["client_credentials"]
    assert {"client-confidential-asymmetric", "permission-v2"} <= set(sc["capabilities"])
    assert "system/DocumentReference.c" in sc["scopes_supported"]
    assert "response_types_supported" not in sc
    assert sc["token_endpoint"].startswith("http")


async def test_metadata_instantiates_the_ig_actors(client):
    cs = (await client.get("/metadata")).json()
    assert "http://hl7.eu/fhir/health-data-api/CapabilityStatement/document-access-provider-submission-option-eu-api" \
        in cs["instantiates"]
    assert cs["rest"][0]["security"]["service"][0]["coding"][0]["code"] == "SMART-on-FHIR"
    pat = next(r for r in cs["rest"][0]["resource"] if r["type"] == "Patient")
    assert "http://hl7.eu/fhir/base/StructureDefinition/patient-eu-core" in pat["supportedProfile"]
    assert "active" in {p["name"] for p in pat["searchParam"]}


async def test_date_search_on_observation_and_encounter(client, auth_headers, pid):
    def total(r):
        assert r.status_code == 200, r.text
        return r.json()["total"]
    all_obs = total(await client.get(f"/Observation?patient={pid}", headers=auth_headers))
    assert total(await client.get(f"/Observation?patient={pid}&date=2024-03-01", headers=auth_headers)) == all_obs
    assert total(await client.get(f"/Observation?patient={pid}&date=lt2020-01-01", headers=auth_headers)) == 0
    assert total(await client.get(f"/Encounter?patient={pid}&date=le2024-04-12", headers=auth_headers)) >= 1
    assert total(await client.get(f"/Encounter?patient={pid}&date=gt2030-01-01", headers=auth_headers)) == 0


async def test_registration_is_owned_by_its_client(client, test_client_jwk, auth_headers):
    pub = {k: v for k, v in test_client_jwk.items() if k in ("kty", "n", "e")}
    body = {"client_id": "owned-client", "jwk": pub, "scopes": ["system/*.rs"]}
    assert (await client.post("/register-client", json=body)).status_code == 200
    # taking an existing id is refused
    assert (await client.post("/register-client", json=body)).status_code == 409
    # management needs a token issued to that client: none -> 401, someone else's -> 403
    assert (await client.patch("/register-client/owned-client", json={"scopes": ["system/*.write"]})).status_code == 401
    r = await client.patch("/register-client/owned-client", json={"scopes": ["system/*.write"]}, headers=auth_headers)
    assert r.status_code == 403
    assert (await client.delete("/register-client/owned-client", headers=auth_headers)).status_code == 403
    assert (await client.get("/register-client/owned-client")).status_code == 200
