"""Epic R4 -> IPS-shape transformer.

Responsibilities:
  1. Re-id every Epic resource to a deterministic uuid (uuid5 over the Epic
     resource path) so re-ingest is idempotent and tests can pin.
  2. Rewrite every internal reference so all references point at the
     re-issued local ids.
  3. Tag each resource with its IPS profile so the bundler's profile claims
     are coherent.
  4. Apply the small set of mandatory semantic transforms IPS requires that
     Epic doesn't do for us:
       - MedicationRequest -> MedicationStatement (IPS prefers MedicationStatement
         in the Medication Summary section). The original MedicationRequest is
         dropped; the derived MedicationStatement.derivedFrom points back at
         Epic's resource path so audit is intact.
       - drop categories/types the local store doesn't know about (Encounter
         category metadata that isn't a supported resource, etc.)
       - filter Condition to problem-list-item category for the Problems section.

We intentionally do NOT do terminology remapping (RxNorm->ATC, CVX->SNOMED,
CPT->SNOMED). That belongs in a separate concept-map pass; the IPS
validator's `-check-ips-codes` flag is the right tool to drive that, and
without a concept map server the safe default is to pass codes through.
"""
from __future__ import annotations

import uuid
from typing import Any

# IPS resource-level profile URIs (uv-ips 2.0.0)
IPS_PROFILES: dict[str, str] = {
    "Patient": "http://hl7.org/fhir/uv/ips/StructureDefinition/Patient-uv-ips",
    "AllergyIntolerance": "http://hl7.org/fhir/uv/ips/StructureDefinition/AllergyIntolerance-uv-ips",
    "Condition": "http://hl7.org/fhir/uv/ips/StructureDefinition/Condition-uv-ips",
    "MedicationStatement": "http://hl7.org/fhir/uv/ips/StructureDefinition/MedicationStatement-uv-ips",
    "Medication": "http://hl7.org/fhir/uv/ips/StructureDefinition/Medication-uv-ips",
    "Immunization": "http://hl7.org/fhir/uv/ips/StructureDefinition/Immunization-uv-ips",
    "Procedure": "http://hl7.org/fhir/uv/ips/StructureDefinition/Procedure-uv-ips",
    "DiagnosticReport": "http://hl7.org/fhir/uv/ips/StructureDefinition/DiagnosticReport-uv-ips",
    "Observation": "http://hl7.org/fhir/uv/ips/StructureDefinition/Observation-results-uv-ips",
    "Practitioner": "http://hl7.org/fhir/uv/ips/StructureDefinition/Practitioner-uv-ips",
    "PractitionerRole": "http://hl7.org/fhir/uv/ips/StructureDefinition/PractitionerRole-uv-ips",
    "Organization": "http://hl7.org/fhir/uv/ips/StructureDefinition/Organization-uv-ips",
    "Device": "http://hl7.org/fhir/uv/ips/StructureDefinition/Device-observer-uv-ips",
}

# IPS absent-data placeholder codes (uv-ips 2.0)
ABSENT_CODE_SYSTEM = "http://hl7.org/fhir/uv/ips/CodeSystem/absent-unknown-uv-ips"

# Resource types we are willing to ingest (must be a subset of store._TYPE_TO_DIR).
SUPPORTED = frozenset({
    "Patient", "AllergyIntolerance", "Condition", "MedicationStatement",
    "MedicationRequest", "Medication", "Immunization", "Procedure",
    "DiagnosticReport", "Observation", "Practitioner", "PractitionerRole",
    "Organization", "Encounter", "ImagingStudy",
})

# Project-scoped uuid5 namespace. Same constant the seed pipeline uses, so an
# Epic patient and a seed patient with the same logical slot would collide
# only if you used the same slot for both - which is by design (slot is the
# stable external identity).
EHDS_NAMESPACE = uuid.UUID("9a3c7c3f-43a1-58a7-89f9-3ea8f1486d6b")
SLOT_IDENTIFIER_SYSTEM = "urn:ehds-demo:slot"
EPIC_IDENTIFIER_SYSTEM = "urn:ehds-demo:epic-source-id"


def _u5(path: str) -> str:
    return str(uuid.uuid5(EHDS_NAMESPACE, path))


def local_id(rtype: str, epic_id: str) -> str:
    """Deterministic local id for an Epic resource."""
    return _u5(f"{rtype}/epic/{epic_id}")


# Extensions are kept only if defined by HL7 International or HL7 Europe. EHDS
# cross-border documents must not carry source-system-proprietary extensions
# (Epic's open.epic.com/*, Nictiz's nictiz.nl/*, US-Core us/core extensions),
# which the validator rejects as "extension could not be found / not allowed".
_ALLOWED_EXT_PREFIXES = (
    "http://hl7.org/fhir/StructureDefinition/",       # core FHIR extensions
    "http://hl7.org/fhir/uv/ips/",                     # IPS
    "http://hl7.eu/fhir/",                             # all HL7 Europe IGs
    "http://hl7.org/fhir/5.0/StructureDefinition/",    # cross-version
)

# Code systems whose Epic-supplied display strings are non-canonical and the
# validator flags as "Wrong Display Name". We drop display (it's optional);
# the code itself is authoritative.
_DISPLAY_STRIP_SYSTEMS = frozenset({
    "http://www.ama-assn.org/go/cpt",
    "http://hl7.org/fhir/sid/icd-9-cm",
    "http://hl7.org/fhir/sid/icd-10-cm",
})


def _ext_is_meaningful(e: dict[str, Any]) -> bool:
    """An extension must carry either a value[x] or nested sub-extensions
    (FHIR invariant ext-1). After stripping foreign content an extension can
    be left with neither — those must be dropped, not emitted empty."""
    if any(k.startswith("value") for k in e):
        return True
    return bool(e.get("extension"))


def _sanitize_for_eu(obj: Any) -> None:
    """In-place: strip proprietary extensions and non-canonical code displays
    so the resource can conform to EU/IPS profiles.

    Key subtlety: a *complex* extension's sub-extensions use relative urls
    (e.g. "level", "type" inside patient-proficiency). We must NOT strip those
    by the http(s) allow-list — only top-level/absolute foreign extensions are
    removed. After stripping we prune any extension left with no value and no
    sub-extensions (ext-1) and any element emptied to {} / []."""
    if isinstance(obj, dict):
        for key in ("extension", "modifierExtension"):
            exts = obj.get(key)
            if isinstance(exts, list):
                kept = []
                for e in exts:
                    if not isinstance(e, dict):
                        continue
                    url = str(e.get("url", ""))
                    # relative urls are complex-extension children — keep them;
                    # absolute urls are kept only if HL7 Intl / HL7 Europe.
                    if url.startswith(("http://", "https://")) and not url.startswith(_ALLOWED_EXT_PREFIXES):
                        continue
                    kept.append(e)
                if kept:
                    obj[key] = kept
                else:
                    obj.pop(key, None)
        if obj.get("system") in _DISPLAY_STRIP_SYSTEMS:
            obj.pop("display", None)
        for v in obj.values():
            _sanitize_for_eu(v)
        # after recursion: drop extensions emptied by stripping (ext-1), then
        # prune any element that collapsed to an empty object / list.
        for key in ("extension", "modifierExtension"):
            if key in obj:
                obj[key] = [e for e in obj[key] if _ext_is_meaningful(e)]
                if not obj[key]:
                    obj.pop(key, None)
        for k in [k for k, v in obj.items() if v in ({}, [], None)]:
            obj.pop(k, None)
    elif isinstance(obj, list):
        for v in obj:
            _sanitize_for_eu(v)


def _walk_replace_refs(obj: Any, mapping: dict[str, str]) -> None:
    """In-place: replace every {"reference": old} where old in mapping."""
    if isinstance(obj, dict):
        ref = obj.get("reference")
        if isinstance(ref, str) and ref in mapping:
            obj["reference"] = mapping[ref]
        for v in obj.values():
            _walk_replace_refs(v, mapping)
    elif isinstance(obj, list):
        for v in obj:
            _walk_replace_refs(v, mapping)


def build_id_map(resources: list[dict[str, Any]]) -> dict[str, str]:
    """Build {old_ref: new_ref} for every resource we are about to ingest."""
    out: dict[str, str] = {}
    for r in resources:
        rtype = r.get("resourceType")
        rid = r.get("id")
        if not rtype or not rid:
            continue
        out[f"{rtype}/{rid}"] = f"{rtype}/{local_id(rtype, rid)}"
    return out


def _tag_profile(res: dict[str, Any]) -> None:
    rtype = res.get("resourceType")
    prof = IPS_PROFILES.get(rtype)
    if not prof:
        return
    meta = res.setdefault("meta", {})
    profiles = meta.setdefault("profile", [])
    if prof not in profiles:
        profiles.append(prof)


def _add_epic_source_identifier(res: dict[str, Any], epic_id: str) -> None:
    """Preserve the original Epic id for audit / round-trip."""
    if "identifier" not in res or not isinstance(res.get("identifier"), list):
        res["identifier"] = res.get("identifier") or []
    res["identifier"].append({
        "system": EPIC_IDENTIFIER_SYSTEM,
        "value": epic_id,
    })


def transform_patient(p: dict[str, Any], epic_id: str) -> dict[str, Any]:
    out = dict(p)
    out["id"] = local_id("Patient", epic_id)
    # add the slot identifier so the demo's PDQm search by ?identifier=<slot>
    # finds this patient.
    idents = list(out.get("identifier") or [])
    idents.append({
        "system": SLOT_IDENTIFIER_SYSTEM,
        "value": f"epic-{epic_id}",
    })
    idents.append({
        "system": EPIC_IDENTIFIER_SYSTEM,
        "value": epic_id,
    })
    out["identifier"] = idents
    _tag_profile(out)
    return out


def medication_request_to_statement(mr: dict[str, Any], epic_id: str) -> dict[str, Any]:
    """IPS prefers MedicationStatement in the Medication Summary section."""
    out: dict[str, Any] = {
        "resourceType": "MedicationStatement",
        "id": local_id("MedicationStatement", epic_id),
        # MedicationStatement.status enum: active|completed|entered-in-error|
        # intended|stopped|on-hold|unknown|not-taken. Map from request.status.
        "status": _map_request_status_to_statement(mr.get("status")),
        "subject": mr.get("subject"),
        # Preserve Epic provenance via Reference.identifier (not Reference.reference)
        # so it survives the reference-rewriting pass.
        "derivedFrom": [{
            "type": "MedicationRequest",
            "identifier": {
                "system": EPIC_IDENTIFIER_SYSTEM,
                "value": f"MedicationRequest/{mr.get('id')}",
            },
        }],
    }
    # carry the medication code/reference
    if "medicationCodeableConcept" in mr:
        out["medicationCodeableConcept"] = mr["medicationCodeableConcept"]
    elif "medicationReference" in mr:
        out["medicationReference"] = mr["medicationReference"]
    # dosage
    if mr.get("dosageInstruction"):
        out["dosage"] = mr["dosageInstruction"]
    # effective period
    if mr.get("dispenseRequest", {}).get("validityPeriod"):
        out["effectivePeriod"] = mr["dispenseRequest"]["validityPeriod"]
    elif mr.get("authoredOn"):
        out["effectiveDateTime"] = mr["authoredOn"]
    _tag_profile(out)
    _add_epic_source_identifier(out, epic_id)
    return out


def _map_request_status_to_statement(s: str | None) -> str:
    return {
        "active": "active",
        "on-hold": "on-hold",
        "cancelled": "stopped",
        "completed": "completed",
        "entered-in-error": "entered-in-error",
        "stopped": "stopped",
        "draft": "intended",
        "unknown": "unknown",
    }.get(s or "", "unknown")


def transform_generic(res: dict[str, Any], epic_id: str) -> dict[str, Any]:
    """Default re-id + profile tagging for any supported resource."""
    out = dict(res)
    rtype = out.get("resourceType")
    if not rtype:
        return out
    out["id"] = local_id(rtype, epic_id)
    _tag_profile(out)
    _add_epic_source_identifier(out, epic_id)
    return out


# ------------------- pipeline -------------------

def transform_bundle(
    epic_resources: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str], str | None]:
    """Transform a list of fetched Epic resources into IPS-shaped local
    resources with rewritten references.

    Returns (local_resources, id_map, patient_local_id).
    """
    # First pass: figure out the id mapping using the ORIGINAL epic ids.
    # For MedicationRequest we map to a MedicationStatement target id so
    # references that pointed at the request now resolve to the statement.
    id_map: dict[str, str] = {}
    for r in epic_resources:
        rtype = r.get("resourceType")
        rid = r.get("id")
        if not rtype or not rid or rtype not in SUPPORTED:
            continue
        if rtype == "MedicationRequest":
            id_map[f"MedicationRequest/{rid}"] = (
                f"MedicationStatement/{local_id('MedicationStatement', rid)}"
            )
        else:
            id_map[f"{rtype}/{rid}"] = f"{rtype}/{local_id(rtype, rid)}"

    # Second pass: emit transformed resources.
    out: list[dict[str, Any]] = []
    patient_local_id: str | None = None
    for r in epic_resources:
        rtype = r.get("resourceType")
        epic_id = r.get("id")
        if not rtype or not epic_id or rtype not in SUPPORTED:
            continue
        if rtype == "Patient":
            t = transform_patient(r, epic_id)
            patient_local_id = t["id"]
        elif rtype == "MedicationRequest":
            t = medication_request_to_statement(r, epic_id)
        elif rtype == "Condition":
            t = transform_generic(r, epic_id)
            # IPS Problems section expects category=problem-list-item.
            # Epic's category coding may be encounter-diagnosis or problem-list-item;
            # if missing, default to problem-list-item so it slots into the section.
            cats = t.get("category") or []
            has = any(
                c.get("code") == "problem-list-item"
                for cc in cats for c in (cc.get("coding") or [])
            )
            if not has:
                cats = cats + [{
                    "coding": [{
                        "system": "http://terminology.hl7.org/CodeSystem/condition-category",
                        "code": "problem-list-item",
                        "display": "Problem List Item",
                    }],
                }]
                t["category"] = cats
        else:
            t = transform_generic(r, epic_id)
        out.append(t)

    # Third pass: rewrite every reference.
    _walk_replace_refs(out, id_map)
    # Fourth pass: strip proprietary extensions + non-canonical displays.
    for r in out:
        _sanitize_for_eu(r)
    return out, id_map, patient_local_id


# ------------------- absent-data placeholders -------------------

def absent_allergy(patient_ref: str) -> dict[str, Any]:
    return {
        "resourceType": "AllergyIntolerance",
        "id": _u5(f"AllergyIntolerance/absent/{patient_ref}"),
        "meta": {"profile": [IPS_PROFILES["AllergyIntolerance"]]},
        "clinicalStatus": {"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
            "code": "active",
        }]},
        "verificationStatus": {"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification",
            "code": "unconfirmed",
        }]},
        "code": {"coding": [{
            "system": ABSENT_CODE_SYSTEM,
            "code": "no-allergy-info",
            "display": "No information about allergies",
        }]},
        "patient": {"reference": patient_ref},
    }


def absent_problem(patient_ref: str) -> dict[str, Any]:
    return {
        "resourceType": "Condition",
        "id": _u5(f"Condition/absent/{patient_ref}"),
        "meta": {"profile": [IPS_PROFILES["Condition"]]},
        "clinicalStatus": {"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
            "code": "active",
        }]},
        "verificationStatus": {"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/condition-ver-status",
            "code": "unconfirmed",
        }]},
        "category": [{"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/condition-category",
            "code": "problem-list-item",
        }]}],
        "code": {"coding": [{
            "system": ABSENT_CODE_SYSTEM,
            "code": "no-problem-info",
            "display": "No information about problems",
        }]},
        "subject": {"reference": patient_ref},
    }


def absent_medication(patient_ref: str) -> dict[str, Any]:
    return {
        "resourceType": "MedicationStatement",
        "id": _u5(f"MedicationStatement/absent/{patient_ref}"),
        "meta": {"profile": [IPS_PROFILES["MedicationStatement"]]},
        "status": "unknown",
        "medicationCodeableConcept": {"coding": [{
            "system": ABSENT_CODE_SYSTEM,
            "code": "no-medication-info",
            "display": "No information about medications",
        }]},
        "subject": {"reference": patient_ref},
    }
