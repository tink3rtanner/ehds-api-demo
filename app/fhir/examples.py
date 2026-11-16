"""One source of truth for every example URL the server publishes.

The smart-configuration ``example_endpoints``, the root discovery document,
``/llms.txt`` and the UI's ``/ui/api/examples`` all read from here, so an
example can never point at an id that does not exist (design goal 5 in
docs/ui-design.md: "every URL shown is live and correct"). Ids are resolved
from the store at request time; if the reference panel is missing the
patient-bound examples are simply omitted rather than invented.
"""
from __future__ import annotations

from typing import Any

from app.config import settings
from app.fhir import store
from app.fhir.capability import PROFILE_EU_BUNDLE, PROFILE_EU_MPD
from app.fhir.document import CATEGORY_TO_DOC_TYPE
from app.fhir.ids import SLOT_IDENTIFIER_SYSTEM, bundle_id, patient_id
from app.fhir.origin import REFERENCE, country_of_patient, origin_of

CATEGORIES: tuple[str, ...] = tuple(CATEGORY_TO_DOC_TYPE.keys())

CATEGORY_NAMES = {
    "patient-summary": "Patient Summary",
    "laboratory-report": "Laboratory Report",
    "discharge-report": "Hospital Discharge Report",
    "imaging-report": "Imaging Report",
}
# the LOINC code to search by, per the EU Health Data API IG search examples
SEARCH_TYPE = {
    "patient-summary": "60591-5",
    "laboratory-report": "11502-2",
    "discharge-report": "18842-5",
    "imaging-report": "85430-7",
}

# every code a seeded DocumentReference.type carries (scripts/seed.py DOC_TYPES + DOC_TYPE_ALSO)
DOC_TYPE_CODES = {
    "patient-summary": ["60591-5"],
    "laboratory-report": ["11502-2"],
    "discharge-report": ["34105-7", "18842-5"],
    "imaging-report": ["18748-4", "85430-7"],
}

# Anna Müller, slot p-001: the scenario's default patient.
DEFAULT_SLOT = "p-001"


def slot_of(patient: dict[str, Any]) -> str | None:
    for ident in patient.get("identifier") or []:
        if ident.get("system") == SLOT_IDENTIFIER_SYSTEM:
            return ident.get("value")
    return None


def reference_patient(slot: str = DEFAULT_SLOT) -> dict[str, Any] | None:
    """The reference patient for ``slot``, else any reference patient, else None."""
    p = store.read("Patient", patient_id(slot))
    if p is not None:
        return p
    for cand in sorted(store.list_all("Patient"), key=lambda r: slot_of(r) or "~"):
        if origin_of(cand)["kind"] == REFERENCE and slot_of(cand):
            return cand
    return None


def patient_summary_card(p: dict[str, Any]) -> dict[str, Any]:
    name = (p.get("name") or [{}])[0]
    return {
        "id": p["id"],
        "slot": slot_of(p),
        "family": name.get("family", ""),
        "given": " ".join(name.get("given") or []),
        "birthDate": p.get("birthDate"),
        "gender": p.get("gender"),
        "country": country_of_patient(p),
        "city": ((p.get("address") or [{}])[0]).get("city"),
    }


def live_examples() -> dict[str, Any]:
    """Every example the server advertises, with real ids baked in."""
    base = settings.base_url
    out: dict[str, Any] = {
        "base_url": base,
        "categories": list(CATEGORIES),
        "endpoints": {
            "capability_statement": f"{base}/metadata",
            "smart_configuration": f"{base}/.well-known/smart-configuration",
            "jwks": f"{base}/.well-known/jwks.json",
            "token": f"{base}/token",
            "register_client": f"{base}/register-client",
            "publish_iti105": f"{base}/DocumentReference",
            "quickstart_script": f"{base}/quickstart.py",
            "all_bundle_uuids": f"{base}/spec/all-bundle-ids",
        },
        "slot_identifier_system": SLOT_IDENTIFIER_SYSTEM,
    }
    p = reference_patient()
    if p is None:
        return out
    card = patient_summary_card(p)
    pid, slot = card["id"], card["slot"] or DEFAULT_SLOT
    out["patient"] = card
    out["endpoints"].update({
        "lookup_patient_by_slot": f"{base}/Patient?identifier={SLOT_IDENTIFIER_SYSTEM}|{slot}",
        "search_patient_by_demographics":
            f"{base}/Patient?family={card['family']}&birthdate={card['birthDate']}",
        "patient_match": f"{base}/Patient/$match  (POST Parameters)",
        "read_patient": f"{base}/Patient/{pid}",
        "patient_summary_operation": f"{base}/Patient/{pid}/$summary",
        "observations_for_patient": f"{base}/Observation?patient={pid}",
        "allergies_for_patient": f"{base}/AllergyIntolerance?patient={pid}",
        "observations_by_patient_identifier":
            f"{base}/Observation?patient.identifier={SLOT_IDENTIFIER_SYSTEM}|{slot}",
        "document_search": f"{base}/DocumentReference?patient={pid}",
        "document_search_by_identifier":
            f"{base}/DocumentReference?patient.identifier={SLOT_IDENTIFIER_SYSTEM}|{slot}",
        "document_retrieve": f"{base}/Bundle/{bundle_id(slot, 'patient-summary')}",
        "eprescription_for_patient": f"{base}/MedicationRequest?patient={pid}",
        "edispensation_for_patient": f"{base}/MedicationDispense?patient={pid}",
    })
    # one working query per EHDS priority category (Article 14): four document
    # types found by ITI-67 `type`, two resource types read through the API.
    out["priority_categories"] = {
        **{cat: {
            "kind": "document",
            "name": CATEGORY_NAMES[cat],
            "loinc": SEARCH_TYPE[cat],
            # other codes on the stored DocumentReference.type (either finds it)
            "also": [c for c in DOC_TYPE_CODES[cat] if c != SEARCH_TYPE[cat]],
            "query": f"{base}/DocumentReference?patient={pid}&type=http://loinc.org|{SEARCH_TYPE[cat]}",
            "retrieve": f"{base}/Bundle/{bundle_id(slot, cat)}",
            "profile": PROFILE_EU_BUNDLE.get(cat),
        } for cat in CATEGORIES},
        "eprescription": {
            "kind": "resource", "name": "ePrescription", "resourceType": "MedicationRequest",
            "query": f"{base}/MedicationRequest?patient={pid}",
            "profile": PROFILE_EU_MPD["MedicationRequest"],
        },
        "edispensation": {
            "kind": "resource", "name": "eDispensation", "resourceType": "MedicationDispense",
            "query": f"{base}/MedicationDispense?patient={pid}",
            "profile": PROFILE_EU_MPD["MedicationDispense"],
        },
    }
    out["documents"] = {
        cat: {
            "bundle_id": bundle_id(slot, cat),
            "path": f"/Bundle/{bundle_id(slot, cat)}",
            "url": f"{base}/Bundle/{bundle_id(slot, cat)}",
            "loinc": CATEGORY_TO_DOC_TYPE[cat]["code"],
            "display": CATEGORY_TO_DOC_TYPE[cat]["display"],
            "profile": PROFILE_EU_BUNDLE.get(cat),
        }
        for cat in CATEGORIES
    }
    out["match_parameters"] = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "resource", "resource": {
                "resourceType": "Patient",
                "name": [{"family": card["family"], "given": [card["given"]]}],
                "birthDate": card["birthDate"],
                "gender": card["gender"],
            }},
            {"name": "onlyCertainMatches", "valueBoolean": False},
            {"name": "count", "valueInteger": 3},
        ],
    }
    return out
