"""healthz + smart-configuration discovery."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_healthz(client):
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_smart_configuration(client):
    r = await client.get("/.well-known/smart-configuration")
    assert r.status_code == 200
    body = r.json()
    assert body["token_endpoint"].endswith("/token")
    assert "client_credentials" in body["grant_types_supported"]
    assert "private_key_jwt" in body["token_endpoint_auth_methods_supported"]


async def test_jwks(client):
    r = await client.get("/.well-known/jwks.json")
    assert r.status_code == 200
    body = r.json()
    assert "keys" in body
    assert all("kid" in k for k in body["keys"])
