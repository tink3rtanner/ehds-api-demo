"""EEHRxF Document Publisher actor.

acts as the producer side: builds DocumentReference + Bundle document and
makes them retrievable. we don't have a separate publisher process — the
seeded DocRefs + the on-demand bundle compile play that role.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

CATEGORIES = ["patient-summary", "laboratory-report", "discharge-report", "imaging-report"]


@pytest.mark.parametrize("category", CATEGORIES)
async def test_publisher_emits_docref_with_eu_profile(client, auth_headers, category):
    r = await client.get("/DocumentReference", headers=auth_headers,
                         params={"patient": "p-001", "category": category})
    assert r.status_code == 200
    body = r.json()
    seed = [e for e in body["entry"] if e["resource"]["id"].startswith("dr-p-")]
    assert len(seed) == 1
    docref = seed[0]["resource"]
    profiles = docref.get("meta", {}).get("profile", [])
    assert any("eu-health-data-api" in p for p in profiles), profiles


@pytest.mark.parametrize("category", CATEGORIES)
async def test_publisher_emits_bundle_with_composition_first(client, auth_headers, category):
    r = await client.get(f"/Binary/doc-p-001-{category}", headers=auth_headers)
    bundle = r.json()
    assert bundle["entry"][0]["resource"]["resourceType"] == "Composition"


@pytest.mark.parametrize("category", CATEGORIES)
async def test_published_bundle_has_required_metadata(client, auth_headers, category):
    bundle = (await client.get(f"/Binary/doc-p-001-{category}", headers=auth_headers)).json()
    assert "id" in bundle
    assert "timestamp" in bundle
    assert "type" in bundle and bundle["type"] == "document"
    assert "meta" in bundle and "profile" in bundle["meta"]
    assert "identifier" in bundle


async def test_publisher_links_docref_to_binary(client, auth_headers):
    docref = (await client.get("/DocumentReference/dr-p-001-patient-summary",
                               headers=auth_headers)).json()
    url = docref["content"][0]["attachment"]["url"]
    assert url == "Binary/doc-p-001-patient-summary"
    r = await client.get(f"/{url}", headers=auth_headers)
    assert r.status_code == 200
