"""build a CapabilityStatement for /metadata.

design choice: the statement is constructed from a static spec dict that
mirrors what the routers implement. when a new resource handler lands, add
its row here; tests assert this stays in sync with the actual route table.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.config import settings

# Canonical EU document-Bundle profile URLs, verified against the published
# IG packages (hl7.eu/fhir/<ig>/package.tgz, 2026-05). The canonical form is
# `http://hl7.eu/fhir/<ig>/...` — NOT `/fhir/ig/<ig>/...` (the old value 404'd
# in the HL7 validator). See docs/epic-eu-bundling.md for the full inventory.
#
# NOTE: `prescription` has no document-Bundle profile in R4. The R4
# `hl7.fhir.eu.mpd` IG ships resource profiles only (MedicationRequest-eu-mpd
# etc.) and its example bundles are `type: collection`. So its value is None:
# the compiler still emits a prescription document Bundle, but it must NOT claim
# `MedicationRequest-eu-mpd` as `Bundle.meta.profile` — that is a *resource*
# profile, and stamping it on a Bundle makes the validator validate the whole
# Bundle as a MedicationRequest (every `Bundle.*` element becomes "not allowed").
# A profile-less prescription Bundle validates correctly as a base-R4 document.
PROFILE_EU_BUNDLE: dict[str, str | None] = {
    "patient-summary":   "http://hl7.eu/fhir/eps/StructureDefinition/bundle-eu-eps",
    "laboratory-report": "http://hl7.eu/fhir/laboratory/StructureDefinition/Bundle-eu-lab",
    "discharge-report":  "http://hl7.eu/fhir/hdr/StructureDefinition/bundle-eu-hdr",
    "imaging-report":    "http://hl7.eu/fhir/imaging/StructureDefinition/BundleReportEuImaging",
    "prescription":      None,
}

# Canonical EU Composition profile URLs per document category (entry[0]).
PROFILE_EU_COMPOSITION = {
    "patient-summary":   "http://hl7.eu/fhir/eps/StructureDefinition/composition-eu-eps",
    "laboratory-report": "http://hl7.eu/fhir/laboratory/StructureDefinition/Composition-eu-lab",
    "discharge-report":  "http://hl7.eu/fhir/hdr/StructureDefinition/composition-eu-hdr",
    "imaging-report":    "http://hl7.eu/fhir/imaging/StructureDefinition/CompositionEuImaging",
}

EHDS_DOCREF_PROFILE = "http://hl7.eu/fhir/health-data-api/StructureDefinition/DocumentReference-eu-eehrxf"

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
            {"name": "summary", "definition": "http://hl7.org/fhir/uv/ips/OperationDefinition/summary"},
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
    # DocumentReference advertises the MHD ITI-67 Document Responder search set
    # as SHALL (euridice-org PR #87). Params are dict-form so each carries an
    # explicit FHIR type and a capabilitystatement-expectation. The XDS-era
    # context params (setting/facility/event) and author.* are implemented by
    # CHAINING to the referenced Encounter/Practitioner — see
    # app/routers/docref.py and docs/document-search-chaining.md.
    "DocumentReference": {
        "interactions": ["read", "search-type"],
        "searchParams": [
            {"name": "_id", "type": "token", "expectation": "SHALL"},
            {"name": "patient", "type": "reference", "expectation": "SHALL"},
            {"name": "patient.identifier", "type": "token", "expectation": "SHALL"},
            {"name": "identifier", "type": "token", "expectation": "SHALL"},
            {"name": "category", "type": "token", "expectation": "SHALL"},
            {"name": "type", "type": "token", "expectation": "SHALL"},
            {"name": "status", "type": "token", "expectation": "SHALL"},
            {"name": "date", "type": "date", "expectation": "SHALL"},
            {"name": "creation", "type": "date", "expectation": "SHALL"},
            {"name": "period", "type": "date", "expectation": "SHALL"},
            {"name": "_lastupdated", "type": "date", "expectation": "SHALL"},
            {"name": "format", "type": "token", "expectation": "SHALL"},
            {"name": "security-label", "type": "token", "expectation": "SHALL"},
            {"name": "related", "type": "reference", "expectation": "SHALL"},
            {"name": "setting", "type": "token", "expectation": "SHALL"},
            {"name": "facility", "type": "token", "expectation": "SHALL"},
            {"name": "event", "type": "token", "expectation": "SHALL"},
            {"name": "author.given", "type": "string", "expectation": "SHALL"},
            {"name": "author.family", "type": "string", "expectation": "SHALL"},
        ],
        "supportedProfile": [EHDS_DOCREF_PROFILE],
    },
    # Binary is kept as a 301 redirect to /Bundle/{id} for legacy URLs; no
    # standalone Binary resources are served. Bundle gets both read (compiled
    # documents) and create (ITI-105 submission).
    "Bundle": {"interactions": ["read", "create"], "searchParams": ["_id"]},
}


_EXPECTATION_URL = "http://hl7.org/fhir/StructureDefinition/capabilitystatement-expectation"
_TOKEN_PARAMS = {"gender", "status", "clinical-status", "category", "intent", "code"}


def _search_param_entry(p: str | dict[str, Any]) -> dict[str, Any]:
    """Build one CapabilityStatement.rest.resource.searchParam entry.

    Accepts either a bare name (legacy, type inferred) or a dict
    ``{"name", "type", "expectation"}``. A dict's ``expectation`` is emitted as
    the capabilitystatement-expectation extension (SHALL/SHOULD/MAY), which is
    how the MHD Document Responder conformance level is declared (PR #87).
    """
    if isinstance(p, dict):
        out: dict[str, Any] = {"name": p["name"], "type": p.get("type", "string")}
        if exp := p.get("expectation"):
            out["extension"] = [{"url": _EXPECTATION_URL, "valueCode": exp}]
        return out
    ptype = "token" if "identifier" in p or p in _TOKEN_PARAMS else "string"
    return {"name": p, "type": ptype}


def _resource_entries() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rtype, spec in FIRST_CLASS_RESOURCES.items():
        entry: dict[str, Any] = {
            "type": rtype,
            "interaction": [{"code": code} for code in spec["interactions"]],
        }
        if spec.get("searchParams"):
            entry["searchParam"] = [_search_param_entry(p) for p in spec["searchParams"]]
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
            "description": (
                "EHDS Demo FHIR Server — synthetic data. SMART Backend Services. "
                "Self-service client registration at /register-client. "
                "Implementer guide at /ui/#/implement. OpenAPI at /openapi.json. "
                "Five EHDS priority categories compiled on demand at /Bundle/{uuid} "
                "(see /spec/all-bundle-ids for the full uuid list, or "
                "/Patient/{id}/$summary for the canonical IPS operation)."
            ),
            "url": settings.base_url,
        },
        "fhirVersion": "4.0.1",
        "format": ["application/fhir+json", "json"],
        "implementationGuide": [
            "http://hl7.eu/fhir/base/ImplementationGuide/hl7.fhir.eu.base",
            "http://hl7.eu/fhir/eps/ImplementationGuide/hl7.fhir.eu.eps",
            "http://hl7.eu/fhir/laboratory/ImplementationGuide/hl7.fhir.eu.laboratory",
            "http://hl7.eu/fhir/hdr/ImplementationGuide/hl7.fhir.eu.hdr",
            "http://hl7.eu/fhir/imaging/ImplementationGuide/hl7.fhir.eu.imaging",
            "http://hl7.eu/fhir/mpd/ImplementationGuide/hl7.fhir.eu.mpd",
            "http://hl7.eu/fhir/health-data-api/ImplementationGuide/hl7.fhir.eu.health-data-api",
        ],
        "rest": [{
            "mode": "server",
            "security": {
                "description": (
                    "SMART Backend Services (private_key_jwt). Discovery: "
                    "/.well-known/smart-configuration. Register a client: "
                    f"POST {settings.base_url}/register-client. "
                    "Mint a bearer: POST {token_endpoint} with grant_type="
                    "client_credentials and a signed JWT client_assertion."
                ),
                "service": [{
                    "coding": [{
                        "system": "http://terminology.hl7.org/CodeSystem/restful-security-service",
                        "code": "SMART-on-FHIR",
                    }],
                }],
                "extension": [{
                    "url": "http://fhir-registry.smarthealthit.org/StructureDefinition/oauth-uris",
                    "extension": [
                        {"url": "token",    "valueUri": settings.token_endpoint},
                        {"url": "register", "valueUri": settings.base_url + "/register-client"},
                    ],
                }],
            },
            "resource": _resource_entries(),
            "operation": [
                {"name": "match", "definition": "http://hl7.org/fhir/OperationDefinition/Patient-match"},
            ],
        }],
    }
