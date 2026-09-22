"""Coverage map data (app/fhir/coverage.py) and its UI endpoints."""
from __future__ import annotations

import json
import os
import time

import pytest

from app.config import settings
from app.fhir import coverage as cov
from app.fhir import validation_queue as vq


def _bundle(bundle_id: str, *, country: str | None, loinc: str, source: str | None = None,
            timestamp: str | None = None) -> dict:
    patient = {"resourceType": "Patient", "id": "p"}
    if country:
        patient["address"] = [{"country": country}]
    b = {"resourceType": "Bundle", "type": "document", "id": bundle_id, "entry": [
        {"resource": {"resourceType": "Composition", "id": "c",
                      "type": {"coding": [{"system": "http://loinc.org", "code": loinc}]}}},
        {"resource": patient},
        {"resource": {"resourceType": "Condition", "id": "x", "subject": {"reference": "Patient/p"}}},
    ]}
    if source:
        b["meta"] = {"source": source}
    if timestamp:
        b["timestamp"] = timestamp
    return b


@pytest.fixture
def inbox(tmp_path_factory):
    """Fresh inbox + validation dir for each test, restored afterwards."""
    d = settings.data_dir / "inbox"
    d.mkdir(parents=True, exist_ok=True)
    before = set(p.name for p in d.glob("*.json"))
    vq._reset_for_tests()
    yield d
    for p in d.glob("*.json"):
        if p.name not in before:
            p.unlink()
    vq._reset_for_tests()


def _put(inbox, bundle: dict, *, age_seconds: float = 0) -> None:
    fp = inbox / f"{bundle['id']}.json"
    fp.write_text(json.dumps(bundle))
    if age_seconds:
        t = time.time() - age_seconds
        os.utime(fp, (t, t))


def test_submissions_are_summarised_newest_first(inbox):
    _put(inbox, _bundle("old", country="Austria", loinc="60591-5", source="https://epic.example/api/FHIR/R4"),
         age_seconds=3600)
    _put(inbox, _bundle("new", country="PL", loinc="34105-7"))
    subs = [s for s in cov.submissions() if s["id"] in ("old", "new")]
    assert [s["id"] for s in subs] == ["new", "old"]
    old = subs[1]
    assert old["country"] == "AT" and old["country_how"] == "patient" and old["european"] is True
    assert old["country_name"] == "Austria"
    assert old["category"] == "patient-summary"
    assert old["source_host"] == "epic.example"
    assert old["resource_types"] == {"Composition": 1, "Condition": 1, "Patient": 1}
    assert old["entry_count"] == 3
    assert old["validation"] == {"state": "unknown"}
    assert subs[0]["category"] == "discharge-report"


def test_submission_without_country_is_unknown_not_guessed(inbox):
    _put(inbox, _bundle("nocountry", country=None, loinc="11502-2"))
    s = next(s for s in cov.submissions() if s["id"] == "nocountry")
    assert s["country"] is None and s["country_how"] is None and s["european"] is False
    c = cov.coverage()
    assert c["unknown_country_submissions"] >= 1


def test_coverage_reference_tier_and_community_tier(inbox):
    _put(inbox, _bundle("at-1", country="AT", loinc="60591-5"))
    _put(inbox, _bundle("at-2", country="AT", loinc="11502-2"))
    _put(inbox, _bundle("us-1", country="US", loinc="60591-5"))
    # mark one as validated via a record on disk
    vq._write("at-1", {"kind": "submission", "submission": "at-1", "state": "validated", "errors": 0, "warnings": 1})
    c = cov.coverage()
    at = c["countries"]["AT"]
    # reference tier: seeded Anna Müller (AT) has DocumentReferences for all 5 categories
    assert at["reference"]["categories"] == c["categories"]
    assert at["community"]["submissions"] == 2
    assert at["community"]["categories"] == {"patient-summary": 1, "laboratory-report": 1}
    assert at["community"]["validated"] == 1
    assert at["score"] == 2
    assert at["complete"] is False
    # a seeded country with no community data is NOT scored
    de = c["countries"]["DE"]
    assert de["reference"]["categories"] and de["community"]["submissions"] == 0 and de["score"] == 0
    # non-European goes beside the map
    assert "US" in c["non_european"]
    assert c["countries"]["US"]["european"] is False
    assert c["totals"]["countries_with_community_data"] >= 1
    assert c["totals"]["countries_with_reference_data"] == 10
    assert c["totals"]["validated_submissions"] >= 1


def test_complete_requires_every_category_validated(inbox):
    for cat_code in ("60591-5", "11502-2", "18842-5", "18748-4", "57833-6"):
        bid = f"fi-{cat_code}"
        _put(inbox, _bundle(bid, country="FI", loinc=cat_code))
        vq._write(bid, {"kind": "submission", "submission": bid, "state": "validated", "errors": 0})
    fi = cov.coverage()["countries"]["FI"]
    assert fi["score"] == 5 and fi["complete"] is True


@pytest.mark.asyncio
async def test_coverage_and_submission_endpoints(client, inbox):
    _put(inbox, _bundle("ep-1", country="SE", loinc="60591-5", source="https://src.example/fhir"))
    r = await client.get("/ui/api/coverage")
    assert r.status_code == 200
    assert r.json()["countries"]["SE"]["community"]["sources"] == ["src.example"]
    r = await client.get("/ui/api/submissions")
    assert any(s["id"] == "ep-1" for s in r.json()["submissions"])
    r = await client.get("/ui/api/submissions/ep-1")
    assert r.status_code == 200
    body = r.json()
    assert body["country"] == "SE"
    assert body["resources"] == []  # nothing was written to the store for this hand-placed inbox file
    assert (await client.get("/ui/api/submissions/does-not-exist")).status_code == 404
    assert (await client.get("/ui/api/submissions/../etc")).status_code in (404, 400)


@pytest.mark.asyncio
async def test_submission_validate_endpoint_queues_or_reports_unavailable(client, inbox):
    _put(inbox, _bundle("ep-2", country="SE", loinc="60591-5"))
    r = await client.post("/ui/api/submissions/ep-2/validate")
    assert r.status_code == 202
    assert r.json()["state"] in ("pending", "unavailable")  # unavailable under the test env (no jar)
    rec = await client.get("/ui/api/validation/ep-2")
    assert rec.status_code == 200 and rec.json()["attempts"] == 1
    assert (await client.post("/ui/api/submissions/nope/validate")).status_code == 404


@pytest.mark.asyncio
async def test_reference_validation_endpoints(client, pid):
    vq._reset_for_tests()
    r = await client.get(f"/ui/api/validation/reference/{pid}/patient-summary")
    assert r.json()["state"] == "none"
    r = await client.post(f"/ui/api/validation/reference/{pid}/patient-summary")
    assert r.status_code == 202
    assert r.json()["kind"] == "reference"
    r = await client.get(f"/ui/api/validation/reference/{pid}/patient-summary")
    assert r.json()["state"] in ("pending", "unavailable")
    assert (await client.post(f"/ui/api/validation/reference/{pid}/nope")).status_code == 404
    assert (await client.post("/ui/api/validation/reference/not-a-patient/patient-summary")).status_code == 404
    vq._reset_for_tests()
