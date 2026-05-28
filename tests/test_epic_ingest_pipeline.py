"""End-to-end test of the Epic ingest pipeline with a mocked Epic source.

Proves the whole pipe works without hitting fhir.epic.com:
  fake Epic compartment
    -> ingest_patient()
    -> store.write()
    -> compile_document(patient_id, 'patient-summary')
    -> Bundle.type=document with EU EPS profile, Composition first, sections
       wired to the IPS-shaped resources, deterministic ids.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from app.fhir import store
from app.fhir.document import compile_document
from app.sources import epic_ingest
from app.sources.epic_transform import EPIC_IDENTIFIER_SYSTEM, SLOT_IDENTIFIER_SYSTEM


@pytest.fixture(autouse=True)
def _scrub_epic_imports_after_test():
    """Remove any resources this test file wrote, so subsequent tests that
    count the synthetic 10-patient panel aren't poisoned. We identify our
    writes by the EPIC_IDENTIFIER_SYSTEM identifier we stamp on every
    ingested resource."""
    yield
    for rtype in list(store.SUPPORTED_TYPES):
        try:
            d = store.dir_for_type(rtype)
        except KeyError:
            continue
        if not d.exists():
            continue
        for fp in list(d.glob("*.json")):
            try:
                import json
                res = json.loads(fp.read_text())
            except Exception:
                continue
            idents = res.get("identifier") or []
            if any(i.get("system") == EPIC_IDENTIFIER_SYSTEM for i in idents):
                fp.unlink(missing_ok=True)
        store.invalidate_cache(rtype)


class FakeEpicClient:
    """Test-double that returns canned Epic-shaped resources by type."""

    def __init__(self, patient: dict[str, Any], compartment: dict[str, list[dict[str, Any]]]):
        self._patient = patient
        self._compartment = compartment

    def read(self, rtype: str, rid: str) -> dict[str, Any]:
        assert rtype == "Patient" and rid == self._patient["id"]
        return self._patient

    def search(self, rtype: str, params: dict[str, Any] | None = None, max_pages: int = 50) -> Iterator[dict[str, Any]]:
        yield from self._compartment.get(rtype, [])


def _epic_compartment(pid: str = "test-epic-pid-full"):
    pat = {
        "resourceType": "Patient",
        "id": pid,
        "name": [{"family": "Lopez", "given": ["Camila"]}],
        "gender": "female",
        "birthDate": "1987-09-12",
    }
    cond = {
        "resourceType": "Condition",
        "id": "cond-t2dm",
        "clinicalStatus": {"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
            "code": "active",
        }]},
        "verificationStatus": {"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/condition-ver-status",
            "code": "confirmed",
        }]},
        "category": [{"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/condition-category",
            "code": "problem-list-item",
        }]}],
        "code": {"coding": [{"system": "http://snomed.info/sct", "code": "44054006",
                              "display": "Type 2 diabetes"}]},
        "subject": {"reference": f"Patient/{pid}"},
    }
    allergy = {
        "resourceType": "AllergyIntolerance",
        "id": "alg-pcn",
        "clinicalStatus": {"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
            "code": "active",
        }]},
        "verificationStatus": {"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification",
            "code": "confirmed",
        }]},
        "code": {"coding": [{"system": "http://snomed.info/sct", "code": "373270004",
                              "display": "Penicillin allergy"}]},
        "patient": {"reference": f"Patient/{pid}"},
    }
    mr = {
        "resourceType": "MedicationRequest",
        "id": "mr-metformin",
        "status": "active",
        "intent": "order",
        "medicationCodeableConcept": {"coding": [{
            "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
            "code": "860975",
        }]},
        "subject": {"reference": f"Patient/{pid}"},
        "authoredOn": "2025-03-14",
    }
    return pat, {
        "Condition": [cond],
        "AllergyIntolerance": [allergy],
        "MedicationRequest": [mr],
    }


def test_full_pipeline():
    # the conftest already redirects EHDS_DATA_DIR to a per-session temp dir;
    # we use a unique Epic id so this test's writes are isolated by uuid.
    patient, compartment = _epic_compartment("test-epic-pid-full")
    client = FakeEpicClient(patient, compartment)
    summary = epic_ingest.ingest_patient(client, "test-epic-pid-full")

    # patient landed in the store with the deterministic id
    assert summary.patient_id
    stored_pat = store.read("Patient", summary.patient_id)
    assert stored_pat is not None
    systems = {i["system"] for i in stored_pat["identifier"]}
    assert SLOT_IDENTIFIER_SYSTEM in systems
    assert EPIC_IDENTIFIER_SYSTEM in systems

    # condition + allergy stored; medicationrequest became MedicationStatement
    assert summary.counts.get("Condition", 0) >= 1
    assert summary.counts.get("AllergyIntolerance", 0) >= 1
    assert summary.counts.get("MedicationStatement", 0) >= 1
    assert summary.counts.get("MedicationRequest", 0) == 0

    # compile the EHDS Patient Summary bundle
    bundle = compile_document(summary.patient_id, "patient-summary")
    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "document"
    # EU EPS profile on the bundle
    assert any("eu" in p for p in bundle["meta"]["profile"])
    # Composition first
    composition = bundle["entry"][0]["resource"]
    assert composition["resourceType"] == "Composition"
    # subject points at our ingested patient
    assert composition["subject"]["reference"] == f"Patient/{summary.patient_id}"
    # sections include the IPS-required three
    section_codes = {
        c["code"]
        for s in composition["section"]
        for c in s.get("code", {}).get("coding", [])
    }
    # 48765-2 allergies, 11450-4 problems, 10160-0 medications
    assert {"48765-2", "11450-4", "10160-0"} <= section_codes


def test_pipeline_handles_absent_sections():
    """A patient with no clinical data still gets a valid bundle with absent-data placeholders."""
    patient, _ = _epic_compartment("test-epic-pid-absent")
    client = FakeEpicClient(patient, {})  # patient only, no compartment
    summary = epic_ingest.ingest_patient(client, "test-epic-pid-absent")

    # absent placeholders inserted by ingest
    assert summary.counts.get("AllergyIntolerance", 0) >= 1
    assert summary.counts.get("Condition", 0) >= 1
    assert summary.counts.get("MedicationStatement", 0) >= 1

    bundle = compile_document(summary.patient_id, "patient-summary")
    composition = bundle["entry"][0]["resource"]
    section_codes = {
        c["code"]
        for s in composition["section"]
        for c in s.get("code", {}).get("coding", [])
    }
    assert {"48765-2", "11450-4", "10160-0"} <= section_codes
