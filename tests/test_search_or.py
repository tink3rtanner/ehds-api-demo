"""FHIR search: a comma in a token value means OR (`type=a,b` = type a or type b)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

L = "http://loinc.org"


async def _ids(client, h, path, **params):
    r = await client.get(path, headers=h, params={**params, "_count": "1000"})
    assert r.status_code == 200, r.text[:300]
    return {e["resource"]["id"] for e in r.json().get("entry", [])}


async def test_document_type_comma_is_or(client, auth_headers):
    ps = await _ids(client, auth_headers, "/DocumentReference", type=f"{L}|60591-5")
    lab = await _ids(client, auth_headers, "/DocumentReference", type=f"{L}|11502-2")
    assert ps and lab
    assert await _ids(client, auth_headers, "/DocumentReference", type=f"{L}|60591-5,{L}|11502-2") == ps | lab


async def test_document_status_comma_is_or(client, auth_headers, pid):
    cur = await _ids(client, auth_headers, "/DocumentReference", patient=pid, status="current")
    assert cur
    assert await _ids(client, auth_headers, "/DocumentReference", patient=pid, status="superseded,current") == cur


async def test_resource_code_comma_is_or(client, auth_headers, pid):
    obs = (await client.get("/Observation", headers=auth_headers, params={"patient": pid})).json()["entry"]
    codes = sorted({e["resource"]["code"]["coding"][0]["code"] for e in obs})[:2]
    assert len(codes) == 2
    one = await _ids(client, auth_headers, "/Observation", patient=pid, code=f"{L}|{codes[0]}")
    two = await _ids(client, auth_headers, "/Observation", patient=pid, code=f"{L}|{codes[1]}")
    both = await _ids(client, auth_headers, "/Observation", patient=pid, code=f"{L}|{codes[0]},{L}|{codes[1]}")
    assert one and two and both == one | two
