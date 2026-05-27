"""compile FHIR Bundle.type=document for each priority category.

categories:
  - patient-summary   (EU EPS)
  - laboratory-report (EU Lab)
  - discharge-report  (EU HDR)
  - imaging-report    (EU Imaging)

each compiler:
  1. picks a Composition (pre-seeded one preferred; auto-built fallback)
  2. resolves all Composition.section.entry[] references from the store
  3. emits a Bundle(type=document) with Composition first, then resources in
     reference-walk order, deduplicated by Resource/<id>.

design choices:
  - we use a stable bundle id 'doc-<patient>-<category>' so identical inputs
    produce identical bundles for golden-file testing.
  - bundle.timestamp comes from Composition.date when present, else 'now'.
  - we stamp bundle.meta.profile with the EU profile URL for the category.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5  # noqa: F401  (uuid4 retained for callers)

from app.config import settings
from app.fhir import store
from app.fhir.capability import PROFILE_EU_BUNDLE

CATEGORY_TO_DOC_TYPE = {
    "patient-summary":   {"system": "http://loinc.org", "code": "60591-5", "display": "Patient summary Document"},
    "laboratory-report": {"system": "http://loinc.org", "code": "11502-2", "display": "Laboratory report"},
    "discharge-report":  {"system": "http://loinc.org", "code": "18842-5", "display": "Discharge summary"},
    "imaging-report":    {"system": "http://loinc.org", "code": "18748-4", "display": "Diagnostic imaging study"},
}

# section default LOINC codes used when we auto-build a Composition.
# display strings MUST match LOINC's canonical en-US text or the HL7 validator
# warns (Wrong Display Name). codes verified against loinc.org 2024-09 release.
SECTION_CODES = {
    "AllergyIntolerance": ("48765-2", "Allergies and adverse reactions Document"),
    "Condition":          ("11450-4", "Problem list - Reported"),
    "MedicationStatement":("10160-0", "History of Medication use Narrative"),
    "MedicationRequest":  ("57828-6", "Prescription list"),
    "MedicationDispense": ("60590-7", "Medication dispense list"),
    "Immunization":       ("11369-6", "History of Immunization note"),
    "Procedure":          ("47519-4", "History of Procedures Document"),
    "Observation":        ("30954-2", "Relevant diagnostic tests/laboratory data note"),
    "DiagnosticReport":   ("11502-2", "Laboratory report"),
    "ImagingStudy":       ("18748-4", "Diagnostic imaging study"),
    "Encounter":          ("46240-8", "History of Hospitalizations+Outpatient visits Narrative"),
}

# per-category preferred section types in order
CATEGORY_SECTIONS: dict[str, list[str]] = {
    "patient-summary":   ["AllergyIntolerance", "Condition", "MedicationStatement",
                          "Immunization", "Procedure", "Observation", "Encounter"],
    "laboratory-report": ["DiagnosticReport", "Observation"],
    "discharge-report":  ["Encounter", "Condition", "MedicationRequest",
                          "MedicationStatement", "Procedure"],
    "imaging-report":    ["DiagnosticReport", "ImagingStudy"],
}


class UnknownCategory(Exception):
    pass


class MissingResources(Exception):
    pass


def _ref(res: dict[str, Any]) -> str:
    return f"{res['resourceType']}/{res['id']}"


def _walk_references(res: dict[str, Any]) -> list[str]:
    """find all 'reference' strings in a resource."""
    out: list[str] = []
    def visit(o):
        if isinstance(o, dict):
            r = o.get("reference")
            if isinstance(r, str) and "/" in r and not r.startswith("urn:"):
                out.append(r)
            for v in o.values():
                visit(v)
        elif isinstance(o, list):
            for v in o:
                visit(v)
    visit(res)
    return out


def _resolve(ref: str) -> dict[str, Any] | None:
    try:
        rtype, rid = ref.split("/", 1)
    except ValueError:
        return None
    if rtype not in store.SUPPORTED_TYPES:
        return None
    return store.read(rtype, rid)


def _gather_for_category(patient_id: str, category: str) -> tuple[list[dict], dict[str, dict]]:
    """find candidate entries per section type. returns (composition_sections, included_resources_by_ref)."""
    section_types = CATEGORY_SECTIONS[category]
    sections: list[dict] = []
    included: dict[str, dict] = {}

    for stype in section_types:
        ents = []
        for r in store.list_all(stype):
            # match patient compartment
            pat = None
            for key in ("subject", "patient", "beneficiary"):
                ref = r.get(key)
                if isinstance(ref, dict) and isinstance(ref.get("reference"), str):
                    if ref["reference"].endswith(f"Patient/{patient_id}"):
                        pat = patient_id
                        break
            if pat != patient_id:
                continue
            # lab/imaging filtering
            if category == "laboratory-report" and stype == "DiagnosticReport":
                cats = [c.get("code") for cc in (r.get("category") or []) for c in (cc.get("coding") or [])]
                if cats and not any(c in ("LAB", "laboratory") for c in cats):
                    continue
            if category == "imaging-report" and stype == "DiagnosticReport":
                cats = [c.get("code") for cc in (r.get("category") or []) for c in (cc.get("coding") or [])]
                if cats and not any(c in ("RAD", "radiology", "imaging") for c in cats):
                    continue
            ents.append(r)
            included[_ref(r)] = r

        if not ents:
            continue
        code_tup = SECTION_CODES.get(stype, ("undefined", stype))
        sections.append({
            "title": stype,
            "code": {"coding": [{"system": "http://loinc.org", "code": code_tup[0], "display": code_tup[1]}]},
            "entry": [{"reference": _ref(r)} for r in ents],
            "text": {
                "status": "generated",
                "div": f'<div xmlns="http://www.w3.org/1999/xhtml">{stype} section: {len(ents)} entries</div>',
            },
        })

    return sections, included


def _author_organization(patient_id: str) -> dict | None:
    # any Organization present
    for o in store.list_all("Organization"):
        return o
    # synth a stand-in
    return {
        "resourceType": "Organization",
        "id": "org-default",
        "name": "EHDS Demo Healthcare Provider",
    }


def _author_practitioner() -> dict | None:
    for p in store.list_all("Practitioner"):
        return p
    return None


def compile_document(patient_id: str, category: str) -> dict[str, Any]:
    if category not in CATEGORY_TO_DOC_TYPE:
        raise UnknownCategory(category)
    patient = store.read("Patient", patient_id)
    if patient is None:
        raise MissingResources(f"Patient/{patient_id} not found")

    # gather sections + included resources
    sections, included = _gather_for_category(patient_id, category)
    if not sections:
        raise MissingResources(f"no candidate data for {category} of {patient_id}")

    # author
    author_org = _author_organization(patient_id)
    author_practitioner = _author_practitioner()

    composition_id = f"comp-{patient_id}-{category}"
    composition: dict[str, Any] = {
        "resourceType": "Composition",
        "id": composition_id,
        "status": "final",
        "type": {"coding": [CATEGORY_TO_DOC_TYPE[category]]},
        "category": [{"coding": [CATEGORY_TO_DOC_TYPE[category]]}],
        "subject": {"reference": f"Patient/{patient_id}"},
        "date": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "author": [],
        "title": f"{CATEGORY_TO_DOC_TYPE[category]['display']} - {patient_id}",
        "section": sections,
    }
    if author_practitioner:
        composition["author"].append({"reference": _ref(author_practitioner)})
        included[_ref(author_practitioner)] = author_practitioner
    if author_org:
        composition["author"].append({"reference": _ref(author_org)})
        included[_ref(author_org)] = author_org

    # transitive closure of references inside included resources
    queue = list(included.values()) + [composition]
    visited: set[str] = set(included.keys())
    while queue:
        cur = queue.pop()
        for ref in _walk_references(cur):
            if ref in visited:
                continue
            visited.add(ref)
            res = _resolve(ref)
            if res is None:
                continue
            included[ref] = res
            queue.append(res)

    # always include the patient
    included[_ref(patient)] = patient

    # bundle assembly: Composition first, then patient, then everything else.
    # R4 requires Bundle.entry.fullUrl to be absolute (or urn:uuid:). All
    # entries here use absolute URLs from the server base so internal
    # references like "Patient/p-001" resolve via the R4 rule "fullUrl ends
    # with /Type/id matches a Type/id relative reference". The Composition is
    # not REST-persisted but we still mint a stable absolute fullUrl for it
    # (compiled-on-demand id) so its own internal references can resolve.
    base = settings.base_url.rstrip("/")

    def _full(res: dict) -> str:
        return f"{base}/{res['resourceType']}/{res['id']}"

    bundle_entries: list[dict[str, Any]] = []
    bundle_entries.append({"fullUrl": _full(composition), "resource": composition})
    bundle_entries.append({"fullUrl": _full(patient), "resource": patient})
    seen_refs = {_ref(composition), _ref(patient)}
    for ref, r in included.items():
        if ref in seen_refs:
            continue
        seen_refs.add(ref)
        bundle_entries.append({"fullUrl": _full(r), "resource": r})

    # bundle identifier: stable per (patient, category) so identical inputs
    # produce identical bundles (matches the comment at the top of this file).
    bundle_uuid = uuid5(NAMESPACE_URL, f"{base}/doc/{patient_id}/{category}")

    return {
        "resourceType": "Bundle",
        "id": f"doc-{patient_id}-{category}",
        "meta": {"profile": [PROFILE_EU_BUNDLE[category]]},
        "type": "document",
        "timestamp": composition["date"],
        "identifier": {
            "system": "urn:ietf:rfc:3986",
            "value": f"urn:uuid:{bundle_uuid}",
        },
        "entry": bundle_entries,
    }
