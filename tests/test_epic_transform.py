"""Unit tests for the Epic -> IPS transformer (no network)."""
from __future__ import annotations

from app.sources.epic_transform import (
    EPIC_IDENTIFIER_SYSTEM,
    IPS_PROFILES,
    SLOT_IDENTIFIER_SYSTEM,
    local_id,
    transform_bundle,
)

EPIC_PID = "erXuFYUfucBZaryVksYEcMg3"


def _epic_patient():
    return {
        "resourceType": "Patient",
        "id": EPIC_PID,
        "name": [{"family": "Lopez", "given": ["Camila", "Maria"]}],
        "gender": "female",
        "birthDate": "1987-09-12",
    }


def _epic_condition(epic_cid: str, code: str = "44054006"):
    return {
        "resourceType": "Condition",
        "id": epic_cid,
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
            "code": "encounter-diagnosis",
        }]}],
        "code": {"coding": [{
            "system": "http://snomed.info/sct", "code": code, "display": "Diabetes mellitus type 2",
        }]},
        "subject": {"reference": f"Patient/{EPIC_PID}"},
    }


def _epic_med_request(epic_mrid: str):
    return {
        "resourceType": "MedicationRequest",
        "id": epic_mrid,
        "status": "active",
        "intent": "order",
        "medicationCodeableConcept": {"coding": [{
            "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
            "code": "860975",
            "display": "Metformin 500 MG Oral Tablet",
        }]},
        "subject": {"reference": f"Patient/{EPIC_PID}"},
        "authoredOn": "2025-03-14",
        "dosageInstruction": [{"text": "1 tablet PO BID"}],
    }


def test_patient_is_re_id_d_and_tagged():
    locals_, _id_map, pid = transform_bundle([_epic_patient()])
    assert pid is not None
    assert pid == local_id("Patient", EPIC_PID)
    pat = next(r for r in locals_ if r["resourceType"] == "Patient")
    assert pat["id"] == pid
    # IPS profile stamped
    assert IPS_PROFILES["Patient"] in pat["meta"]["profile"]
    # slot + epic-source identifiers added
    systems = {i["system"] for i in pat["identifier"]}
    assert SLOT_IDENTIFIER_SYSTEM in systems
    assert EPIC_IDENTIFIER_SYSTEM in systems


def test_references_rewritten_to_local_ids():
    locals_, _id_map, pid = transform_bundle([
        _epic_patient(),
        _epic_condition("cond-1"),
    ])
    cond = next(r for r in locals_ if r["resourceType"] == "Condition")
    assert cond["subject"]["reference"] == f"Patient/{pid}"


def test_medication_request_becomes_medication_statement():
    locals_, _id_map, _pid = transform_bundle([
        _epic_patient(),
        _epic_med_request("mr-1"),
    ])
    rtypes = [r["resourceType"] for r in locals_]
    assert "MedicationStatement" in rtypes
    assert "MedicationRequest" not in rtypes
    ms = next(r for r in locals_ if r["resourceType"] == "MedicationStatement")
    assert ms["status"] == "active"
    assert ms["medicationCodeableConcept"]["coding"][0]["code"] == "860975"
    # derivedFrom preserves Epic source reference via Reference.identifier
    df = ms["derivedFrom"][0]
    assert df["identifier"]["value"] == "MedicationRequest/mr-1"
    assert df["identifier"]["system"] == "urn:ehds-demo:epic-source-id"
    # IPS profile stamped
    assert IPS_PROFILES["MedicationStatement"] in ms["meta"]["profile"]


def test_condition_gets_problem_list_item_category_when_missing():
    epic_cond = _epic_condition("cond-2")
    # strip categories to simulate a Condition without category
    epic_cond["category"] = []
    locals_, _id_map, _pid = transform_bundle([_epic_patient(), epic_cond])
    cond = next(r for r in locals_ if r["resourceType"] == "Condition")
    codes = {c.get("code") for cc in cond["category"] for c in cc.get("coding", [])}
    assert "problem-list-item" in codes


def test_local_ids_are_deterministic():
    locals_a, _, _ = transform_bundle([_epic_patient(), _epic_condition("c1")])
    locals_b, _, _ = transform_bundle([_epic_patient(), _epic_condition("c1")])
    by_type_a = {r["resourceType"]: r["id"] for r in locals_a}
    by_type_b = {r["resourceType"]: r["id"] for r in locals_b}
    assert by_type_a == by_type_b
