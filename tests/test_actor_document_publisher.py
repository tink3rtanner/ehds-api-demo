"""EEHRxF Document Publisher actor.

acts as the producer side: builds DocumentReference + Bundle document and
makes them retrievable. we don't have a separate publisher process — the
seeded DocRefs + the on-demand bundle compile play that role.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

from app.fhir.capability import PROFILE_EU_BUNDLE
from app.fhir.document import CATEGORY_TO_DOC_TYPE
from app.fhir.ids import bundle_id, docref_id

CATEGORIES = list(CATEGORY_TO_DOC_TYPE.keys())

# deterministic uuids for the seed DocumentReferences of every (patient, category)
SEED_DOCREF_IDS = {docref_id("p-001", c) for c in CATEGORIES}


@pytest.mark.parametrize("category", CATEGORIES)
async def test_publisher_emits_docref_with_eu_profile(client, auth_headers, category):
    # select each category's seed DocumentReference by its LOINC document `type`
    # (a unique per-category code) — `.category` is no longer the priority slug
    # and is absent entirely for patient-summary / prescription (PR #88).
    r = await client.get("/DocumentReference", headers=auth_headers,
                         params={"patient": "p-001", "type": CATEGORY_TO_DOC_TYPE[category]["code"]})
    assert r.status_code == 200
    body = r.json()
    seed = [e for e in body["entry"] if e["resource"]["id"] in SEED_DOCREF_IDS]
    assert len(seed) == 1
    docref = seed[0]["resource"]
    profiles = docref.get("meta", {}).get("profile", [])
    # canonical EHDS DocumentReference profile (hl7.eu/fhir/health-data-api/...)
    assert any("health-data-api" in p for p in profiles), profiles


@pytest.mark.parametrize("category", CATEGORIES)
async def test_publisher_emits_bundle_with_composition_first(client, auth_headers, category):
    r = await client.get(f"/Bundle/{bundle_id('p-001', category)}", headers=auth_headers)
    bundle = r.json()
    assert bundle["entry"][0]["resource"]["resourceType"] == "Composition"


@pytest.mark.parametrize("category", CATEGORIES)
async def test_published_bundle_has_required_metadata(client, auth_headers, category):
    bundle = (await client.get(f"/Bundle/{bundle_id('p-001', category)}", headers=auth_headers)).json()
    assert "id" in bundle
    assert "timestamp" in bundle
    assert "type" in bundle and bundle["type"] == "document"
    # prescription has no document-Bundle profile (PROFILE_EU_BUNDLE None); the
    # rest must stamp one.
    if PROFILE_EU_BUNDLE[category] is None:
        assert "profile" not in (bundle.get("meta") or {})
    else:
        assert "meta" in bundle and "profile" in bundle["meta"]
    assert "identifier" in bundle


async def test_publisher_links_docref_to_bundle(client, auth_headers):
    docref = (await client.get(f"/DocumentReference/{docref_id('p-001', 'patient-summary')}",
                               headers=auth_headers)).json()
    url = docref["content"][0]["attachment"]["url"]
    expected = f"Bundle/{bundle_id('p-001', 'patient-summary')}"
    assert url == expected, f"{url} != {expected}"
    r = await client.get(f"/{url}", headers=auth_headers)
    assert r.status_code == 200
