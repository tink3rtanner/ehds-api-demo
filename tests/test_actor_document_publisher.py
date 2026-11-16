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
async def test_seed_docref_claims_no_profile(client, auth_headers, category):
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
    # the EU Health Data API IG defines no DocumentReference profile, so a
    # seeded DocumentReference claims none (it must not claim one that does not exist)
    assert not docref.get("meta", {}).get("profile"), docref.get("meta")


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
    assert bundle["meta"]["profile"] == [PROFILE_EU_BUNDLE[category]]
    assert "identifier" in bundle


async def test_publisher_links_docref_to_bundle(client, auth_headers):
    docref = (await client.get(f"/DocumentReference/{docref_id('p-001', 'patient-summary')}",
                               headers=auth_headers)).json()
    url = docref["content"][0]["attachment"]["url"]
    expected = f"Bundle/{bundle_id('p-001', 'patient-summary')}"
    assert url == expected, f"{url} != {expected}"
    r = await client.get(f"/{url}", headers=auth_headers)
    assert r.status_code == 200
