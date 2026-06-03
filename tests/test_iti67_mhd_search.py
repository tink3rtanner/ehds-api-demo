"""MHD ITI-67 DocumentReference search — conformance for the SHALL set.

Covers euridice-org eu-health-data-api PR #87 (ITI-67 / MHD alignment: the
Document Responder search parameters become SHALL, plus the new `creation`
param) the way THIS server implements them: the XDS-era context params
(`setting`/`facility`/`event`) and `author.*` are NOT denormalised onto the
DocumentReference — they are resolved by CHAINING through the document's
reference graph to the real Encounter / Practitioner the bundle was broken
open into. See app/routers/docref.py and docs/document-search-chaining.md.

The final test is an end-to-end round-trip: submit a document bundle IN
(ITI-105), confirm it is broken open and naturalized per the resource-identity
recipe (re-minted local ids, original ids preserved as `urn:ehds-demo:source-id`,
`meta.source` back-link resolvable via `$source`, internal references rewritten),
and read it back OUT — proving a chained search resolves through the *rewritten*
graph.
"""
from __future__ import annotations

import uuid

import pytest

from app.fhir.ids import child_id, docref_id
from scripts.seed import DOC_TYPES

pytestmark = pytest.mark.asyncio

# the five seed DocumentReferences for slot p-001 (one per priority category).
P001_DOCREFS = {docref_id("p-001", c) for c in DOC_TYPES}


async def _search(client, headers, **params) -> dict:
    r = await client.get("/DocumentReference", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _seed_hits(body: dict) -> set[str]:
    """ids in a searchset that are p-001's seed DocumentReferences."""
    return {e["resource"]["id"] for e in body.get("entry", [])} & P001_DOCREFS


# --------------------------------------------------------------------------
# DocumentReference-local SHALL params (matched on the DR itself).
# Each param is exercised positively (a value that SHALL match all five p-001
# docrefs) and negatively (a value that matches none) — proving the server
# actually processes the param rather than ignoring an unknown one.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("param,hit,miss", [
    ("date",           "2024-04-15",   "2024-04-16"),    # DocumentReference.date
    ("creation",       "2024-04-14",   "2024-04-13"),    # content.attachment.creation
    ("_lastupdated",   "ge2024-01-01", "ge2025-01-01"),  # meta.lastUpdated
    ("format",         "urn:ihe:iti:xds-sd:text:2008", "urn:nope"),  # content.format
    ("security-label", "N",            "R"),             # securityLabel
])
async def test_local_shall_param_is_processed(client, auth_headers, param, hit, miss):
    hit_body = await _search(client, auth_headers, patient="p-001", **{param: hit})
    assert _seed_hits(hit_body) == P001_DOCREFS, f"{param}={hit} should match all p-001 docrefs"
    miss_body = await _search(client, auth_headers, patient="p-001", **{param: miss})
    assert _seed_hits(miss_body) == set(), f"{param}={miss} should match none"


async def test_related_param(client, auth_headers):
    """`related` matches DocumentReference.context.related (here -> the Encounter)."""
    enc_ref = f"Encounter/{child_id('p-001', 'Encounter', 0)}"
    body = await _search(client, auth_headers, patient="p-001", related=enc_ref)
    assert _seed_hits(body) == P001_DOCREFS


async def test_period_chains_to_encounter(client, auth_headers):
    """`period` resolves against the referenced Encounter.period window."""
    hit = await _search(client, auth_headers, patient="p-001", period="ge2024-01-01")
    assert _seed_hits(hit) == P001_DOCREFS
    miss = await _search(client, auth_headers, patient="p-001", period="ge2025-01-01")
    assert _seed_hits(miss) == set()


# --------------------------------------------------------------------------
# Chained SHALL params: resolved through context.encounter -> Encounter and
# author -> Practitioner. For slot p-001 the seeded Encounter[0] is the
# inpatient stay (class IMP, serviceType 394802001, type 32485007) and the
# author Practitioner is "Dr. Marco Bianchi".
# --------------------------------------------------------------------------
@pytest.mark.parametrize("param,hit,miss", [
    ("setting",       "394802001", "000000"),  # Encounter.serviceType (practice setting)
    ("facility",      "IMP",       "AMB"),      # Encounter.class (care-setting type)
    ("event",         "32485007",  "000000"),   # Encounter.type (clinical act)
    ("author.family", "Bianchi",   "Nobody"),   # Practitioner.name.family
    ("author.given",  "Marco",     "Nobody"),   # Practitioner.name.given
])
async def test_chained_shall_param_resolves(client, auth_headers, param, hit, miss):
    hit_body = await _search(client, auth_headers, patient="p-001", **{param: hit})
    assert _seed_hits(hit_body) == P001_DOCREFS, f"{param}={hit} should chain-match all p-001 docrefs"
    miss_body = await _search(client, auth_headers, patient="p-001", **{param: miss})
    assert _seed_hits(miss_body) == set(), f"{param}={miss} should match none"


async def test_author_chain_is_case_insensitive_prefix(client, auth_headers):
    """FHIR string search is case-insensitive starts-with."""
    body = await _search(client, auth_headers, patient="p-001", **{"author.family": "bianch"})
    assert _seed_hits(body) == P001_DOCREFS


# --------------------------------------------------------------------------
# CapabilityStatement declares the Document Responder SHALL set (PR #87).
# --------------------------------------------------------------------------
async def test_capability_advertises_shall_search_params(client):
    body = (await client.get("/metadata")).json()
    dr = next(r for r in body["rest"][0]["resource"] if r["type"] == "DocumentReference")
    params = {p["name"]: p for p in dr["searchParam"]}
    required = [
        "patient", "patient.identifier", "identifier", "category", "type", "status",
        "date", "creation", "period", "_lastupdated", "format", "security-label",
        "related", "setting", "facility", "event", "author.given", "author.family",
    ]
    for name in required:
        assert name in params, f"CapabilityStatement omits DocumentReference search param {name}"
        exts = params[name].get("extension", [])
        assert any(e.get("valueCode") == "SHALL" for e in exts), f"{name} not declared SHALL"


# --------------------------------------------------------------------------
# End-to-end: ITI-105 submit (data IN) -> break open + naturalize -> read OUT,
# asserting the resource-identity recipe AND that a chained search resolves
# through the rewritten reference graph.
# --------------------------------------------------------------------------
async def test_round_trip_breaks_open_bundle_naturalizes_and_chains(client, auth_headers):
    sfx = uuid.uuid4().hex[:8]
    src = "https://source.example/fhir"
    dr_fid, pr_fid, enc_fid = f"dr-{sfx}", f"pr-{sfx}", f"enc-{sfx}"
    # the submission is about a patient NOT pre-registered on this server (the
    # realistic ITI-105 case) — keeps this test fully isolated from the seed
    # panel's compartment counts, and the subject ref (out-of-bundle) must be
    # preserved, not rewritten.
    subject_pid = str(uuid.uuid4())

    bundle = {
        "resourceType": "Bundle",
        "id": f"sub-{sfx}",
        "type": "document",
        "entry": [
            {
                "fullUrl": f"{src}/DocumentReference/{dr_fid}",
                "resource": {
                    "resourceType": "DocumentReference",
                    "id": dr_fid,
                    "status": "current",
                    "type": {"coding": [{"system": "http://loinc.org", "code": "11502-2"}]},
                    "category": [{"coding": [{"system": "http://loinc.org", "code": "26436-6"}]}],
                    "subject": {"reference": f"Patient/{subject_pid}"},
                    # references INTO the bundle, by foreign id — must be rewritten
                    "author": [{"reference": f"Practitioner/{pr_fid}"}],
                    "context": {"encounter": [{"reference": f"Encounter/{enc_fid}"}]},
                    "content": [{"attachment": {"contentType": "application/fhir+json",
                                                "url": f"Bundle/sub-{sfx}"}}],
                },
            },
            {
                "fullUrl": f"{src}/Practitioner/{pr_fid}",
                "resource": {
                    "resourceType": "Practitioner",
                    "id": pr_fid,
                    "name": [{"family": "Zywicki", "given": ["Tomasz"]}],
                },
            },
            {
                "fullUrl": f"{src}/Encounter/{enc_fid}",
                "resource": {
                    "resourceType": "Encounter",
                    "id": enc_fid,
                    "status": "finished",
                    "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                              "code": "AMB", "display": "ambulatory"},
                    "serviceType": {"coding": [{"system": "http://snomed.info/sct", "code": "408478003"}]},
                    "type": [{"coding": [{"system": "http://snomed.info/sct", "code": "11429006"}]}],
                    "subject": {"reference": f"Patient/{subject_pid}"},
                    "period": {"start": "2024-06-01T09:00:00+02:00", "end": "2024-06-01T10:00:00+02:00"},
                },
            },
        ],
    }

    # ---- data IN ----
    r = await client.post("/", headers=auth_headers, json=bundle)
    assert r.status_code == 201, r.text
    locations = {e["response"]["location"].split("/", 1)[0]: e["response"]["location"]
                 for e in r.json()["entry"]}
    assert set(locations) == {"DocumentReference", "Practitioner", "Encounter"}, \
        "all three resources must be broken open and mirrored into the store"

    dr_loc = locations["DocumentReference"]
    pr_loc = locations["Practitioner"]
    local_dr_id = dr_loc.split("/", 1)[1]
    local_pr_id = pr_loc.split("/", 1)[1]
    assert local_dr_id != dr_fid and local_pr_id != pr_fid  # re-minted to local ids

    # ---- read OUT: resource-identity recipe on the DocumentReference ----
    fetched = (await client.get(f"/{dr_loc}", headers=auth_headers)).json()
    # original id preserved as a source identifier
    assert {"system": "urn:ehds-demo:source-id", "value": dr_fid} in fetched["identifier"]
    # internal references REWRITTEN to the local ids (not the foreign ones)
    assert fetched["author"][0]["reference"] == pr_loc
    assert fetched["context"]["encounter"][0]["reference"] == locations["Encounter"]
    # reference to the (out-of-bundle) patient is preserved, not rewritten
    assert fetched["subject"]["reference"] == f"Patient/{subject_pid}"
    # meta.source back-link recorded, and resolvable via $source (307 redirect)
    assert fetched["meta"]["source"] == f"{src}/DocumentReference/{dr_fid}"
    srcresp = await client.get(f"/{dr_loc}/$source", headers=auth_headers)
    assert srcresp.status_code == 307
    assert srcresp.headers["location"] == f"{src}/DocumentReference/{dr_fid}"

    # the broken-open Practitioner is independently addressable, with its own
    # source identifier preserved.
    pr = (await client.get(f"/{pr_loc}", headers=auth_headers)).json()
    assert pr["name"][0]["family"] == "Zywicki"
    assert {"system": "urn:ehds-demo:source-id", "value": pr_fid} in pr["identifier"]

    # ---- chained search resolves through the REWRITTEN graph ----
    # The submitted surname ("Zywicki") and serviceType code ("408478003") are
    # unique to this submission, so an unscoped search isolates the naturalized
    # DocumentReference — proving the chain resolves through the rewritten refs.
    # author.family chains DR.author -> the naturalized Practitioner.
    by_author = await _search(client, auth_headers, **{"author.family": "Zywicki"})
    assert local_dr_id in {e["resource"]["id"] for e in by_author["entry"]}
    # setting chains DR.context.encounter -> the naturalized Encounter.serviceType.
    by_setting = await _search(client, auth_headers, setting="408478003")
    assert local_dr_id in {e["resource"]["id"] for e in by_setting["entry"]}
