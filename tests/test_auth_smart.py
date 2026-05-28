"""SMART backend services auth flow + scope behaviour."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_token_happy_path(client, make_assertion):
    assertion = make_assertion()
    r = await client.post("/token", data={
        "grant_type": "client_credentials",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": assertion,
        "scope": "system/*.read",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["expires_in"] > 0


async def test_token_wrong_grant_type(client, make_assertion):
    r = await client.post("/token", data={
        "grant_type": "password",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": make_assertion(),
        "scope": "system/*.read",
    })
    assert r.status_code == 400


async def test_token_bad_audience(client, make_assertion):
    bad = make_assertion(aud="http://elsewhere/token")
    r = await client.post("/token", data={
        "grant_type": "client_credentials",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": bad,
        "scope": "system/*.read",
    })
    assert r.status_code == 401


async def test_token_iss_sub_mismatch(client, make_assertion):
    bad = make_assertion(iss="evil-client")
    r = await client.post("/token", data={
        "grant_type": "client_credentials",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": bad,
        "scope": "system/*.read",
    })
    # 401 either because client unknown or iss != sub
    assert r.status_code == 401


async def test_token_replay_protection(client, make_assertion):
    a = make_assertion()
    payload = dict(
        grant_type="client_credentials",
        client_assertion_type="urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        client_assertion=a,
        scope="system/*.read",
    )
    r1 = await client.post("/token", data=payload)
    assert r1.status_code == 200
    r2 = await client.post("/token", data=payload)
    assert r2.status_code == 400


async def test_protected_endpoint_with_bad_bearer(client, pid):
    """Sending an Authorization header forces strict validation in any env.
    A bad bearer => 401. (In ENV=dev the server allows truly-anonymous GETs
    for the synthetic-data demo — see app/auth/verify.py — but a present-
    but-invalid bearer never silently passes.)"""
    r = await client.get(f"/Patient/{pid}", headers={"Authorization": "Bearer not-a-real-jwt"})
    assert r.status_code == 401


async def test_protected_endpoint_with_bearer(client, auth_headers, pid):
    r = await client.get(f"/Patient/{pid}", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["resourceType"] == "Patient"
