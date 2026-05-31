"""Naturalization at ingest boundaries + the $source back-link.

Covers the resource-identity design in docs/resource-identity.md:
  * foreign ids are re-minted to local uuid5 (submissions don't pollute the
    panel with foreign-id'd resources),
  * internal references are rewritten so they resolve on THIS host,
  * the original id is preserved as a `urn:ehds-demo:source-id` identifier,
  * `meta.source` records a resolvable path back to the origin, exposed via
    `GET /{Type}/{id}/$source`.
"""
from __future__ import annotations

import pytest

from app.fhir import store
from app.fhir.naturalize import SOURCE_ID_SYSTEM, naturalize_bundle

# ---------------- unit: the naturalize_bundle primitive ----------------

def test_naturalize_reids_and_rewrites_refs():
    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {
                "fullUrl": "urn:uuid:pat-1",
                "resource": {"resourceType": "Patient", "id": "foreign-pat-1",
                             "name": [{"family": "Foreign"}]},
            },
            {
                "fullUrl": "urn:uuid:obs-1",
                "resource": {
                    "resourceType": "Observation", "id": "foreign-obs-1",
                    "status": "final",
                    "code": {"text": "x"},
                    # reference the patient two ways: by Type/id and by fullUrl
                    "subject": {"reference": "Patient/foreign-pat-1"},
                    "performer": [{"reference": "urn:uuid:pat-1"}],
                },
            },
        ],
    }
    out = naturalize_bundle(bundle)
    by_type = {r["resourceType"]: r for r in out}
    pat, obs = by_type["Patient"], by_type["Observation"]

    # ids are re-minted (no longer the foreign ids), deterministic uuid5
    assert pat["id"] != "foreign-pat-1"
    assert obs["id"] != "foreign-obs-1"
    assert naturalize_bundle(bundle)[0]["id"] == pat["id"]  # idempotent

    # both reference forms now point at the LOCAL patient id
    assert obs["subject"]["reference"] == f"Patient/{pat['id']}"
    assert obs["performer"][0]["reference"] == f"Patient/{pat['id']}"

    # origin ids preserved as source identifiers
    assert {"system": SOURCE_ID_SYSTEM, "value": "foreign-pat-1"} in pat["identifier"]
    assert {"system": SOURCE_ID_SYSTEM, "value": "foreign-obs-1"} in obs["identifier"]

    # original bundle is untouched (inbox stays the as-submitted evidence)
    assert bundle["entry"][0]["resource"]["id"] == "foreign-pat-1"


def test_naturalize_demotes_dangling_urn_ref_to_logical():
    # an Observation referencing a urn:uuid that is NOT a supported entry in the
    # bundle → the literal reference can't resolve on-host, so it's demoted to a
    # logical reference (Reference.identifier), not left dangling.
    bundle = {
        "resourceType": "Bundle", "type": "transaction",
        "entry": [{
            "fullUrl": "urn:uuid:obs-1",
            "resource": {
                "resourceType": "Observation", "id": "o1", "status": "final",
                "code": {"text": "x"},
                "device": {"reference": "urn:uuid:not-in-bundle"},
            },
        }],
    }
    obs = naturalize_bundle(bundle)[0]
    assert "reference" not in obs["device"]
    assert obs["device"]["identifier"] == {
        "system": "urn:ietf:rfc:3986", "value": "urn:uuid:not-in-bundle"}


def test_naturalize_meta_source_from_absolute_fullurl():
    base = "https://fhir.example.org/api/FHIR/R4"
    bundle = {
        "resourceType": "Bundle", "type": "transaction",
        "entry": [{
            "fullUrl": f"{base}/Patient/abc",
            "resource": {"resourceType": "Patient", "id": "abc"},
        }],
    }
    out = naturalize_bundle(bundle)
    assert out[0]["meta"]["source"] == f"{base}/Patient/abc"


def test_naturalize_meta_source_from_source_base():
    base = "https://fhir.example.org/api/FHIR/R4"
    bundle = {
        "resourceType": "Bundle", "type": "transaction",
        "entry": [{"resource": {"resourceType": "Patient", "id": "abc"}}],
    }
    out = naturalize_bundle(bundle, source_base=base)
    assert out[0]["meta"]["source"] == f"{base}/Patient/abc"


# ---------------- API: submit -> $source round-trip ----------------

def _submission(source_base_url: str) -> dict:
    return {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [{
            "fullUrl": f"{source_base_url}/Patient/ext-9",
            "resource": {"resourceType": "Patient", "id": "ext-9",
                         "name": [{"family": "Externe"}], "gender": "other"},
            "request": {"method": "PUT", "url": "Patient/ext-9"},
        }],
    }


@pytest.mark.asyncio
async def test_submitted_resource_exposes_source_link(client, auth_headers):
    src = "https://fhir.example.org/api/FHIR/R4"
    r = await client.post("/", headers={**auth_headers, "Content-Type": "application/fhir+json"},
                          json=_submission(src))
    assert r.status_code == 201, r.text
    loc = r.json()["entry"][0]["response"]["location"]   # Patient/<local-uuid>
    local_id = loc.split("/", 1)[1]
    try:
        # meta.source landed on the stored resource
        got = await client.get(f"/{loc}", headers=auth_headers)
        assert got.json()["meta"]["source"] == f"{src}/Patient/ext-9"

        # $source redirects (307) to the origin URL — the "open at source" link
        red = await client.get(f"/Patient/{local_id}/$source",
                               headers=auth_headers, follow_redirects=False)
        assert red.status_code == 307
        assert red.headers["location"] == f"{src}/Patient/ext-9"
    finally:
        (store.dir_for_type("Patient") / f"{local_id}.json").unlink(missing_ok=True)
        store.invalidate_cache("Patient")


@pytest.mark.asyncio
async def test_source_link_404_when_no_source(client, auth_headers, pid):
    # a seeded patient has no meta.source — $source should 404, not redirect
    r = await client.get(f"/Patient/{pid}/$source",
                         headers=auth_headers, follow_redirects=False)
    assert r.status_code == 404
    assert r.json()["resourceType"] == "OperationOutcome"
