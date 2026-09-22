"""origin tagging + country derivation (app/fhir/origin.py).

Every resource the server holds is either a *reference* example (seeded) or a
*community* submission (arrived over ITI-105 / Epic import). The UI badges by
these tags and the coverage map derives its country from the data — never from
who the client was.
"""
from __future__ import annotations

import pytest

from app.fhir import origin


def _patient(country: str | None = "AT") -> dict:
    p = {"resourceType": "Patient", "id": "x", "address": [{"city": "Vienna"}]}
    if country is not None:
        p["address"][0]["country"] = country
    return p


# ---------- tagging ----------

def test_tag_reference_is_idempotent():
    r = {"resourceType": "Condition", "id": "c1"}
    origin.tag_origin(r, origin.REFERENCE)
    origin.tag_origin(r, origin.REFERENCE)
    tags = r["meta"]["tag"]
    assert [t for t in tags if t["system"] == origin.ORIGIN_TAG_SYSTEM] == [
        {"system": origin.ORIGIN_TAG_SYSTEM, "code": "reference", "display": "Reference example"},
    ]


def test_tag_community_records_submission_id():
    r = {"resourceType": "Condition", "id": "c1", "meta": {"source": "https://src.example/fhir/Condition/9"}}
    origin.tag_origin(r, origin.COMMUNITY, submission_id="abc-123")
    o = origin.origin_of(r)
    assert o == {"kind": "community", "submission": "abc-123",
                 "source": "https://src.example/fhir/Condition/9"}


def test_retagging_replaces_previous_origin():
    r = {"resourceType": "Condition", "id": "c1"}
    origin.tag_origin(r, origin.REFERENCE)
    origin.tag_origin(r, origin.COMMUNITY, submission_id="s1")
    assert origin.origin_of(r)["kind"] == "community"
    assert sum(1 for t in r["meta"]["tag"] if t["system"] == origin.ORIGIN_TAG_SYSTEM) == 1


def test_origin_of_untagged_is_unknown():
    assert origin.origin_of({"resourceType": "Patient"}) == {"kind": "unknown", "submission": None, "source": None}


# ---------- country normalisation ----------

@pytest.mark.parametrize("raw,expected", [
    ("AT", "AT"), ("at", "AT"), (" de ", "DE"),
    ("AUT", "AT"), ("DEU", "DE"), ("GBR", "GB"), ("USA", "US"),
    ("Austria", "AT"), ("Österreich", "AT"), ("Deutschland", "DE"), ("Germany", "DE"),
    ("United Kingdom", "GB"), ("UK", "GB"), ("Great Britain", "GB"),
    ("United States", "US"), ("United States of America", "US"),
    ("Nederland", "NL"), ("The Netherlands", "NL"), ("España", "ES"), ("Suomi", "FI"),
    ("Czechia", "CZ"), ("Czech Republic", "CZ"),
    ("", None), (None, None), ("Atlantis", None), ("A", None),
])
def test_normalize_country(raw, expected):
    assert origin.normalize_country(raw) == expected


def test_european_set_is_the_map_set():
    for code in ("AT", "DE", "FR", "IT", "ES", "PL", "SE", "FI", "GB", "IE", "NO", "CH", "UA", "TR", "IS", "MT", "CY"):
        assert origin.is_european(code), code
    for code in ("US", "CA", "JP", "BR", "AU", None, ""):
        assert not origin.is_european(code), code


def test_country_names_cover_every_european_code():
    for code in origin.EUROPEAN_COUNTRIES:
        assert origin.COUNTRY_NAMES.get(code), f"no display name for {code}"


# ---------- derivation from data ----------

def test_country_of_patient_prefers_home_address():
    p = {"resourceType": "Patient", "address": [
        {"use": "work", "country": "DE"},
        {"use": "home", "country": "Austria"},
    ]}
    assert origin.country_of_patient(p) == "AT"


def test_country_of_patient_missing():
    assert origin.country_of_patient(_patient(None)) is None
    assert origin.country_of_patient({"resourceType": "Patient"}) is None


def _bundle(*resources: dict) -> dict:
    return {"resourceType": "Bundle", "type": "document",
            "entry": [{"resource": r} for r in resources]}


def test_country_of_bundle_from_patient():
    b = _bundle({"resourceType": "Composition", "id": "c"}, _patient("PL"))
    assert origin.country_of_bundle(b) == ("PL", "patient")


def test_country_of_bundle_falls_back_to_custodian_then_any_organization():
    custodian = {"resourceType": "Organization", "id": "org-c", "address": [{"country": "SE"}]}
    other = {"resourceType": "Organization", "id": "org-o", "address": [{"country": "DK"}]}
    comp = {"resourceType": "Composition", "id": "c", "custodian": {"reference": "Organization/org-c"}}
    assert origin.country_of_bundle(_bundle(comp, _patient(None), other, custodian)) == ("SE", "custodian")
    comp_no_cust = {"resourceType": "Composition", "id": "c"}
    assert origin.country_of_bundle(_bundle(comp_no_cust, _patient(None), other)) == ("DK", "organization")


def test_country_of_bundle_never_guesses():
    assert origin.country_of_bundle(_bundle({"resourceType": "Composition", "id": "c"}, _patient(None))) == (None, None)
    assert origin.country_of_bundle({"resourceType": "Bundle"}) == (None, None)


# ---------- category derivation ----------

@pytest.mark.parametrize("code,expected", [
    ("60591-5", "patient-summary"),
    ("11502-2", "laboratory-report"),
    ("18842-5", "discharge-report"),
    ("34105-7", "discharge-report"),   # HDR fixes this code; the Epic pipeline emits it
    ("18748-4", "imaging-report"),
    ("57833-6", "prescription"),
    ("99999-9", None),
])
def test_category_of_bundle_from_document_type(code, expected):
    comp = {"resourceType": "Composition", "id": "c",
            "type": {"coding": [{"system": "http://loinc.org", "code": code}]}}
    assert origin.category_of_bundle(_bundle(comp)) == expected


def test_category_of_bundle_prefers_document_reference_type():
    dr = {"resourceType": "DocumentReference", "id": "d",
          "type": {"coding": [{"system": "http://loinc.org", "code": "11502-2"}]}}
    comp = {"resourceType": "Composition", "id": "c",
            "type": {"coding": [{"system": "http://loinc.org", "code": "60591-5"}]}}
    assert origin.category_of_bundle(_bundle(comp, dr)) == "laboratory-report"


def test_category_of_bundle_without_types():
    assert origin.category_of_bundle(_bundle(_patient())) is None


# ---------- backfill migration ----------

def test_backfill_tags_untagged_community_resources_and_links_submissions(tmp_path, monkeypatch):
    """scripts.backfill_origin: pre-tagging store contents get a community tag,
    linked to the inbox bundle their source-id came from; untouched otherwise."""
    import json

    from app.config import settings
    from app.fhir import store
    from scripts.backfill_origin import backfill

    inbox = settings.data_dir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "sub-bf.json").write_text(json.dumps({"resourceType": "Bundle", "type": "transaction", "entry": [
        {"fullUrl": "urn:uuid:foreign-1", "resource": {"resourceType": "Condition", "id": "foreign-1"}}]}))
    legacy = {"resourceType": "Condition", "id": "bf-legacy-1", "subject": {"reference": "Patient/x"},
              "identifier": [{"system": "urn:ehds-demo:source-id", "value": "foreign-1"}],
              "meta": {"source": "https://src.example/fhir/Condition/foreign-1"}}
    untouched = {"resourceType": "Condition", "id": "bf-legacy-2", "subject": {"reference": "Patient/x"}}
    store.write(legacy)
    store.write(untouched)
    try:
        stats = backfill(settings.data_dir)
        assert stats["tagged-community"] >= 1 and stats["linked-to-submission"] >= 1
        got = store.read("Condition", "bf-legacy-1")
        assert origin.origin_of(got) == {"kind": "community", "submission": "sub-bf",
                                         "source": "https://src.example/fhir/Condition/foreign-1"}
        # no provenance at all is still community (only the seed makes reference data), just unlinked
        assert origin.origin_of(store.read("Condition", "bf-legacy-2")) == {"kind": "community", "submission": None, "source": None}
        assert stats["no-provenance"] >= 1
        # idempotent
        again = backfill(settings.data_dir)
        assert again["tagged-community"] == 0
    finally:
        for rid in ("bf-legacy-1", "bf-legacy-2"):
            (store.dir_for_type("Condition") / f"{rid}.json").unlink(missing_ok=True)
        (inbox / "sub-bf.json").unlink(missing_ok=True)
        store.invalidate_cache()
