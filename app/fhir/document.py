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

import copy
from datetime import UTC, datetime
from typing import Any

from app.config import settings
from app.fhir import store
from app.fhir.capability import PROFILE_EU_BUNDLE, PROFILE_EU_COMPOSITION
from app.fhir.ids import (
    SLOT_IDENTIFIER_SYSTEM,
    bundle_id,
    bundle_identifier,
    composition_id,
    organization_id,
    practitioner_id,
)

CATEGORY_TO_DOC_TYPE = {
    "patient-summary":   {"system": "http://loinc.org", "code": "60591-5", "display": "Patient summary Document"},
    "laboratory-report": {"system": "http://loinc.org", "code": "11502-2", "display": "Laboratory report"},
    # HDR 1.0.0 fixes Composition.type to 34105-7 (patternCodeableConcept).
    "discharge-report":  {"system": "http://loinc.org", "code": "34105-7", "display": "Hospital Discharge summary"},
    "imaging-report":    {"system": "http://loinc.org", "code": "18748-4", "display": "Diagnostic imaging study"},
}
# ePrescription and eDispensation are NOT documents: they are MedicationRequest
# and MedicationDispense resources (HL7 Europe MPD profiles), read through the
# resource API. See docs/conformance-deviations.md §3.

# Composition.category where a profile fixes it. EU Imaging requires two
# slices: LOINC 85430-7 and the EHDS priority category Medical-Imaging.
COMPOSITION_CATEGORY: dict[str, list[dict]] = {
    "imaging-report": [
        {"coding": [{"system": "http://loinc.org", "code": "85430-7", "display": "Diagnostic imaging report"}]},
        {"coding": [{"system": "http://hl7.eu/fhir/health-data-api/CodeSystem/eehrxf-document-priority-category-cs",
                     "code": "Medical-Imaging"}]},
    ],
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


DR_COMPOSITION_EXT = "http://hl7.org/fhir/5.0/StructureDefinition/extension-DiagnosticReport.composition"


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
    lab_results: set[str] = set()
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

        # EU Lab: a laboratory report holds exactly one DiagnosticReport, and
        # its results are that report's Observations.
        if category == "laboratory-report":
            if stype == "DiagnosticReport":
                ents = sorted(ents, key=lambda r: r.get("id", ""))[:1]
                lab_results = {x.get("reference") for d in ents for x in d.get("result") or []}
            elif stype == "Observation":
                ents = [r for r in ents if _ref(r) in lab_results]
        for r in ents:
            included[_ref(r)] = r

        if not ents:
            continue
        # Observations split across IPS sections by category: vital-signs go to
        # the Vital Signs section (8716-3), everything else to Results
        # (30954-2). The EPS Results section requires entries to match
        # medicalTestResult-eu-core, which vital signs don't — so mis-filing
        # them in Results is a validation error.
        if stype == "Observation" and category == "patient-summary":
            vitals = [r for r in ents if _is_vital_sign(r)]
            results = [r for r in ents if not _is_vital_sign(r)]
            if vitals:
                sections.append(_obs_section(vitals, "8716-3", "Vital signs note"))
            if results:
                sections.append(_obs_section(results, "30954-2",
                                             "Relevant diagnostic tests/laboratory data note"))
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


def _is_vital_sign(obs: dict[str, Any]) -> bool:
    return any(c.get("code") == "vital-signs"
               for cc in (obs.get("category") or [])
               for c in (cc.get("coding") or []))


def _obs_section(ents: list[dict], code: str, display: str) -> dict:
    return {
        "title": display,
        "code": {"coding": [{"system": "http://loinc.org", "code": code, "display": display}]},
        "entry": [{"reference": _ref(r)} for r in ents],
        "text": {
            "status": "generated",
            "div": f'<div xmlns="http://www.w3.org/1999/xhtml">{display}: {len(ents)} entries</div>',
        },
    }


# EU EPS Composition required sections (min=1) with their LOINC codes.
# composition-eu-eps slices: sectionProblems, sectionAllergies,
# sectionMedications, sectionProceduresHx, sectionMedicalDevices.
EPS_REQUIRED_SECTIONS = [
    ("11450-4", "Problem list - Reported"),
    ("48765-2", "Allergies and adverse reactions Document"),
    ("10160-0", "History of Medication use Narrative"),
    ("47519-4", "History of Procedures Document"),
    ("46264-8", "History of medical device use"),
]


def _ensure_required_eps_sections(sections: list[dict]) -> None:
    """Append any EPS-required section that's missing, with an emptyReason."""
    present = {
        c.get("code")
        for s in sections
        for c in s.get("code", {}).get("coding", [])
    }
    for code, display in EPS_REQUIRED_SECTIONS:
        if code in present:
            continue
        sections.append({
            "title": display,
            "code": {"coding": [{"system": "http://loinc.org", "code": code, "display": display}]},
            "text": {
                "status": "generated",
                "div": (f'<div xmlns="http://www.w3.org/1999/xhtml">'
                        f'No information available for: {display}.</div>'),
            },
            # IPS/EPS: a required section with no data carries an emptyReason
            # instead of entries.
            "emptyReason": {"coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/list-empty-reason",
                "code": "unavailable",
                "display": "Unavailable",
            }]},
        })


def _narrative_section(title: str, code: str, display: str, body: str,
                       entries: list[dict] | None = None) -> dict:
    sec: dict[str, Any] = {
        "title": title,
        "code": {"coding": [{"system": "http://loinc.org", "code": code, "display": display}]},
        "text": {"status": "generated", "div": f'<div xmlns="http://www.w3.org/1999/xhtml">{body}</div>'},
    }
    if entries:
        sec["entry"] = [{"reference": _ref(r)} for r in entries]
    else:
        # EU Imaging (eu-imaging-composition-1): a section without entries says why
        sec["emptyReason"] = {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/list-empty-reason",
                                          "code": "unavailable", "display": "Unavailable"}]}
    return sec


def _imaging_sections(included: dict[str, dict]) -> list[dict]:
    """The EU Imaging report structure: imaging study, order, history and
    procedure sections (all required), plus impression and the report itself."""
    studies = [r for r in included.values() if r["resourceType"] == "ImagingStudy"]
    reports = [r for r in included.values() if r["resourceType"] == "DiagnosticReport"]
    rep = reports[0] if reports else {}
    exam = (rep.get("code") or {}).get("text") or "Imaging examination"
    modality = ", ".join(m.get("display") or m.get("code", "") for s in studies for m in s.get("modality") or [])
    started = next((s.get("started", "")[:10] for s in studies if s.get("started")), "")
    return [
        _narrative_section("Imaging study", "18726-0", "Radiology studies (set)",
                           f"{exam}{f' ({modality})' if modality else ''}{f', {started}' if started else ''}.",
                           studies),
        _narrative_section("Order", "55115-0", "Requested imaging studies information Document",
                           f"Requested: {exam}."),
        _narrative_section("History", "11329-0", "History general Narrative - Reported",
                           "No relevant history recorded for this examination."),
        _narrative_section("Procedure", "55111-9", "Current imaging procedure descriptions Document",
                           f"{exam} performed{f' on {started}' if started else ''}."),
        _narrative_section("Impression", "19005-8", "Radiology Imaging study [Impression] (narrative)",
                           rep.get("conclusion") or "No impression recorded."),
        _narrative_section("Report", "LP173421-1", "Report", f"{exam}: {rep.get('conclusion') or 'see report'}",
                           reports),
    ]


def _inpatient_encounter(patient_id: str) -> dict | None:
    """The patient's inpatient stay (class IMP), else any of their encounters."""
    mine = [e for e in store.list_all("Encounter")
            if (e.get("subject") or {}).get("reference", "").endswith(f"Patient/{patient_id}")]
    mine.sort(key=lambda e: ((e.get("class") or {}).get("code") != "IMP", e.get("id", "")))
    return mine[0] if mine else None


def _hospital_course_section(encounter: dict | None) -> dict:
    """HDR Hospital course section (LOINC 8648-8), narrative from the stay."""
    if encounter:
        per = encounter.get("period") or {}
        reason = "; ".join(r.get("text", "") for r in encounter.get("reasonCode") or [] if r.get("text"))
        body = (f"Admitted {per.get('start', 'unknown')[:10]}, discharged {per.get('end', 'unknown')[:10]}."
                + (f" Reason for admission: {reason}." if reason else ""))
    else:
        body = "No hospital stay recorded."
    sec: dict[str, Any] = {
        "title": "Hospital course",
        "code": {"coding": [{"system": "http://loinc.org", "code": "8648-8", "display": "Hospital course Narrative"}]},
        "text": {"status": "generated", "div": f'<div xmlns="http://www.w3.org/1999/xhtml">{body}</div>'},
    }
    if encounter:
        sec["entry"] = [{"reference": _ref(encounter)}]
    return sec


def _author_organization(slot: str) -> dict | None:
    """The patient's own care organisation, else any Organization."""
    return store.read("Organization", organization_id(slot)) or next(iter(store.list_all("Organization")), None)


def _author_practitioner(slot: str) -> dict | None:
    """The patient's own practitioner, else any Practitioner."""
    return store.read("Practitioner", practitioner_id(slot)) or next(iter(store.list_all("Practitioner")), None)


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

    # EU EPS requires these Composition sections (min=1). When the patient has
    # no data for a required section we still emit the section with an
    # emptyReason (the IPS/EPS pattern), otherwise the document fails the
    # required-slice constraint.
    if category == "patient-summary":
        _ensure_required_eps_sections(sections)

    # HDR 1.0.0: the Composition SHALL name the hospital stay (Composition.
    # encounter, plus an Encounter entry) and SHALL carry a Hospital course
    # (8648-8) or Discharge summary (18842-5) section (cmp-hdr-2).
    hdr_encounter = _inpatient_encounter(patient_id) if category == "discharge-report" else None
    if category == "discharge-report":
        sections.insert(0, _hospital_course_section(hdr_encounter))
    if category == "imaging-report":
        sections = _imaging_sections(included)

    # bundle/composition uuids are deterministic on the slot label so the
    # mapping {slot, category} -> uuid is stable from outside callers
    slot = next((i["value"] for i in (patient.get("identifier") or [])
                 if i.get("system") == SLOT_IDENTIFIER_SYSTEM), patient_id)

    # author: the patient's own practitioner and organisation
    author_org = _author_organization(slot)
    author_practitioner = _author_practitioner(slot)
    comp_id = composition_id(slot, category)
    composition: dict[str, Any] = {
        "resourceType": "Composition",
        "id": comp_id,
        # EU EPS requires Composition.identifier (1..1). Deterministic per slot+category.
        "identifier": {"system": "urn:ietf:rfc:3986",
                       "value": f"urn:uuid:{composition_id(slot, category + ':identifier')}"},
        "status": "final",
        "type": {"coding": [CATEGORY_TO_DOC_TYPE[category]]},
        # narrative is required at Composition level by IPS/EU profiles
        "text": {
            "status": "generated",
            "div": (f'<div xmlns="http://www.w3.org/1999/xhtml">'
                    f'{CATEGORY_TO_DOC_TYPE[category]["display"]} for patient {patient_id}. '
                    f'Compiled {datetime.now(UTC).strftime("%Y-%m-%d")}.</div>'),
        },
        "category": COMPOSITION_CATEGORY.get(category) or [{"coding": [CATEGORY_TO_DOC_TYPE[category]]}],
        "subject": {"reference": f"Patient/{patient_id}"},
        "date": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "author": [],
        "title": f"{CATEGORY_TO_DOC_TYPE[category]['display']} - {patient_id}",
        "section": sections,
    }
    if hdr_encounter is not None:
        composition["encounter"] = {"reference": _ref(hdr_encounter)}
        included[_ref(hdr_encounter)] = hdr_encounter
    comp_profile = PROFILE_EU_COMPOSITION.get(category)
    if comp_profile:
        composition["meta"] = {"profile": [comp_profile]}
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

    # EU Lab and EU Imaging: the DiagnosticReport points back at its
    # Composition (R5 DiagnosticReport.composition, an extension in R4)
    if category in ("laboratory-report", "imaging-report"):
        for ref, r in list(included.items()):
            if r.get("resourceType") == "DiagnosticReport":
                r = copy.deepcopy(r)
                r["extension"] = [e for e in r.get("extension") or [] if e.get("url") != DR_COMPOSITION_EXT] + [
                    {"url": DR_COMPOSITION_EXT, "valueReference": {"reference": _ref(composition)}}]
                included[ref] = r

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

    bundle = {
        "resourceType": "Bundle",
        "id": bundle_id(slot, category),
        "type": "document",
        # HDR requires Bundle.language; harmless for the other categories
        "language": "en",
        "timestamp": composition["date"],
        "identifier": {
            "system": "urn:ietf:rfc:3986",
            "value": bundle_identifier(slot, category),
        },
        "entry": bundle_entries,
    }
    # every category has an EU document-Bundle profile
    bundle_profile = PROFILE_EU_BUNDLE.get(category)
    if bundle_profile:
        bundle["meta"] = {"profile": [bundle_profile]}
    return bundle
