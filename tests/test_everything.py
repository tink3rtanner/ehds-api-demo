"""Patient/{id}/$everything"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_everything(client, auth_headers):
    r = await client.get("/Patient/p-001/$everything", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resourceType"] == "Bundle"
    assert body["type"] == "searchset"
    types = {e["resource"]["resourceType"] for e in body["entry"]}
    # everything that compartments under the patient
    assert "Patient" in types
    assert "Observation" in types
    assert "Condition" in types
    assert "MedicationStatement" in types
    assert "ImagingStudy" in types


async def test_everything_unknown_patient(client, auth_headers):
    r = await client.get("/Patient/p-zzz/$everything", headers=auth_headers)
    assert r.status_code == 404
