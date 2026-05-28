"""build a CapabilityStatement for /metadata.

design choice: the statement is constructed from a static spec dict that
mirrors what the routers implement. when a new resource handler lands, add
its row here; tests assert this stays in sync with the actual route table.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.config import settings

# canonical EU profile URLs (per the IGs we depend on). EHDS phase-1 priority
# categories that are realistic to compile from an atomic resource store.
PROFILE_EU_BUNDLE = {
    "patient-summary":   "http://hl7.eu/fhir/ig/eps/StructureDefinition/Bundle-eu-eps",
    "laboratory-report": "http://hl7.eu/fhir/ig/laboratory/StructureDefinition/Bundle-eu-lab",
    "discharge-report":  "http://hl7.eu/fhir/ig/hdr/StructureDefinition/Bundle-eu-hdr",
    "imaging-report":    "http://hl7.eu/fhir/ig/imaging/StructureDefinition/Bundle-eu-imaging",
    "prescription":      "http://hl7.eu/fhir/ig/eu-health-data-api/StructureDefinition/Bundle-eu-prescription",
}

EHDS_DOCREF_PROFILE = "http://hl7.eu/fhir/ig/eu-health-data-api/StructureDefinition/DocumentReference-eu-eehrxf"

PDQM_PATIENT_SEARCH_PARAMS = [
    "_id", "identifier", "family", "given", "name", "birthdate", "gender",
    "address", "address-city", "address-postalcode", "address-country", "address-state",
    "telecom", "phone", "email",
]

FIRST_CLASS_RESOURCES: dict[str, dict[str, Any]] = {
    "Patient": {
        "interactions": ["read", "search-type"],
        "searchParams": PDQM_PATIENT_SEARCH_PARAMS,
        "operations": [
            {"name": "match", "definition": "http://hl7.org/fhir/OperationDefinition/Patient-match"},
            {"name": "everything", "definition": "http://hl7.org/fhir/OperationDefinition/Patient-everything"},
        ],
    },
    "AllergyIntolerance": {"interactions": ["read", "search-type"], "searchParams": ["_id", "patient", "clinical-status"]},
    "Condition": {"interactions": ["read", "search-type"], "searchParams": ["_id", "patient", "clinical-status", "category"]},
    "MedicationStatement": {"interactions": ["read", "search-type"], "searchParams": ["_id", "patient", "status"]},
    "MedicationRequest": {"interactions": ["read", "search-type"], "searchParams": ["_id", "patient", "status", "intent"]},
    "MedicationDispense": {"interactions": ["read", "search-type"], "searchParams": ["_id", "patient", "status"]},
    "Medication": {"interactions": ["read", "search-type"], "searchParams": ["_id", "code"]},
    "Immunization": {"interactions": ["read", "search-type"], "searchParams": ["_id", "patient", "status"]},
    "Observation": {"interactions": ["read", "search-type"], "searchParams": ["_id", "patient", "category", "code", "date"]},
    "Procedure": {"interactions": ["read", "search-type"], "searchParams": ["_id", "patient", "status"]},
    "DiagnosticReport": {"interactions": ["read", "search-type"], "searchParams": ["_id", "patient", "category", "code", "date"]},
    "ImagingStudy": {"interactions": ["read", "search-type"], "searchParams": ["_id", "patient", "status"]},
    "Encounter": {"interactions": ["read", "search-type"], "searchParams": ["_id", "patient", "status", "date"]},
    "Specimen": {"interactions": ["read", "search-type"], "searchParams": ["_id", "patient"]},
    "Practitioner": {"interactions": ["read", "search-type"], "searchParams": ["_id", "identifier", "name"]},
    "PractitionerRole": {"interactions": ["read", "search-type"], "searchParams": ["_id", "practitioner", "organization"]},
    "Organization": {"interactions": ["read", "search-type"], "searchParams": ["_id", "identifier", "name"]},
    "Composition": {"interactions": ["read", "search-type"], "searchParams": ["_id", "patient", "type"]},
    "DocumentReference": {
        "interactions": ["read", "search-type"],
        "searchParams": ["_id", "patient", "category", "type", "status", "date"],
        "supportedProfile": [EHDS_DOCREF_PROFILE],
    },
    "Binary": {"interactions": ["read"], "searchParams": []},
    "Bundle": {"interactions": ["create"], "searchParams": []},  # ITI-105 submission target
}


def _resource_entries() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rtype, spec in FIRST_CLASS_RESOURCES.items():
        entry: dict[str, Any] = {
            "type": rtype,
            "interaction": [{"code": code} for code in spec["interactions"]],
        }
        if spec.get("searchParams"):
            entry["searchParam"] = [
                {"name": p, "type": "token" if "identifier" in p or p in ("gender", "status", "clinical-status", "category", "intent") else "string"}
                for p in spec["searchParams"]
            ]
        if spec.get("operations"):
            entry["operation"] = spec["operations"]
        if spec.get("supportedProfile"):
            entry["supportedProfile"] = spec["supportedProfile"]
        out.append(entry)
    return out


def build_capability_statement() -> dict[str, Any]:
    return {
        "resourceType": "CapabilityStatement",
        "url": settings.base_url + "/metadata",
        "version": "0.1.0",
        "name": "EHDSDemoCapabilityStatement",
        "title": "EHDS Demo FHIR Server CapabilityStatement",
        "status": "active",
        "experimental": True,
        "date": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "publisher": "ehds-api contributors",
        "kind": "instance",
        "implementation": {
            "description": "EHDS Demo FHIR Server",
            "url": settings.base_url,
        },
        "fhirVersion": "4.0.1",
        "format": ["application/fhir+json", "json"],
        "implementationGuide": [
            "http://hl7.eu/fhir/ig/eu-core/ImplementationGuide/hl7.fhir.eu.base",
            "http://hl7.eu/fhir/ig/eps/ImplementationGuide/hl7.fhir.eu.eps",
            "http://hl7.eu/fhir/ig/laboratory/ImplementationGuide/hl7.fhir.eu.laboratory",
            "http://hl7.eu/fhir/ig/hdr/ImplementationGuide/hl7.fhir.eu.hdr",
            "http://hl7.eu/fhir/ig/imaging/ImplementationGuide/hl7.fhir.eu.imaging",
            "http://hl7.eu/fhir/ig/eu-health-data-api/ImplementationGuide/hl7.fhir.eu.eehrxf",
        ],
        "rest": [{
            "mode": "server",
            "security": {
                "service": [{
                    "coding": [{
                        "system": "http://terminology.hl7.org/CodeSystem/restful-security-service",
                        "code": "SMART-on-FHIR",
                    }],
                }],
                "extension": [{
                    "url": "http://fhir-registry.smarthealthit.org/StructureDefinition/oauth-uris",
                    "extension": [
                        {"url": "token", "valueUri": settings.token_endpoint},
                    ],
                }],
            },
            "resource": _resource_entries(),
            "operation": [
                {"name": "match", "definition": "http://hl7.org/fhir/OperationDefinition/Patient-match"},
            ],
        }],
    }
