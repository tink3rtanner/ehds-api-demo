"""seed 10 synthetic EU patients + all dependent resources.

deterministic: same inputs -> same outputs (same ids, same dates).
covers every priority category compiler's needs:
  - patient summary  -> Patient, AllergyIntolerance, Condition, MedicationStatement,
                        Immunization, Procedure, Observation, Encounter
  - laboratory       -> DiagnosticReport(LAB), Observation, Specimen
  - discharge        -> Encounter, Condition, MedicationRequest, Procedure
  - imaging          -> DiagnosticReport(RAD), ImagingStudy

usage:  python -m scripts.seed [--data-dir path]
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = REPO_ROOT / "data"

EHDS_DOCREF_PROFILE = "http://hl7.eu/fhir/ig/eu-health-data-api/StructureDefinition/DocumentReference-eu-eehrxf"

# canonical loinc codes for each priority category
DOC_TYPES = {
    "patient-summary":   {"system": "http://loinc.org", "code": "60591-5", "display": "Patient summary Document"},
    "laboratory-report": {"system": "http://loinc.org", "code": "11502-2", "display": "Laboratory report"},
    "discharge-report":  {"system": "http://loinc.org", "code": "18842-5", "display": "Discharge summary"},
    "imaging-report":    {"system": "http://loinc.org", "code": "18748-4", "display": "Diagnostic imaging study"},
}

CATEGORY_CS = "http://hl7.eu/fhir/ig/eu-health-data-api/CodeSystem/eehrxf-document-priority-category"
CATEGORY_CODES = {
    "patient-summary":   {"system": CATEGORY_CS, "code": "patient-summary",   "display": "Patient Summary"},
    "laboratory-report": {"system": CATEGORY_CS, "code": "laboratory-report", "display": "Laboratory Report"},
    "discharge-report":  {"system": CATEGORY_CS, "code": "discharge-report",  "display": "Hospital Discharge Report"},
    "imaging-report":    {"system": CATEGORY_CS, "code": "imaging-report",    "display": "Medical Imaging"},
}

@dataclass
class P:
    pid: str
    family: str
    given: list[str]
    gender: str  # 'male' | 'female' | 'other'
    birthdate: str
    country: str  # ISO-3166-1 alpha-2
    city: str
    postal: str
    line: str
    language: str  # bcp-47
    national_id_system: str  # urn:oid:... or similar
    national_id: str
    phone: str
    email: str


PANEL: list[P] = [
    P("p-001", "Müller",   ["Anna"],      "female", "1968-03-14", "AT", "Vienna",     "1010", "Stephansplatz 1",   "de-AT",
      "urn:oid:1.2.40.0.10.1.4.3.1", "1014031968", "+43 1 5555111", "anna.mueller@example.at"),
    P("p-002", "Schmidt",  ["Hans"],      "male",   "1955-07-29", "DE", "Berlin",     "10115", "Friedrichstr. 100",  "de-DE",
      "urn:oid:1.3.6.1.4.1.21367.13.20.3000", "DE-2907551955", "+49 30 5550199", "hans.schmidt@example.de"),
    P("p-003", "Rossi",    ["Giulia"],    "female", "1981-11-02", "IT", "Rome",       "00184", "Via Cavour 8",       "it-IT",
      "urn:oid:2.16.840.1.113883.2.9.4.3.2", "RSSGLI81S42H501X", "+39 06 5550244", "giulia.rossi@example.it"),
    P("p-004", "Dubois",   ["Lucas"],     "male",   "1972-04-17", "FR", "Paris",      "75003", "Rue de Bretagne 3",  "fr-FR",
      "urn:oid:1.2.250.1.213.1.4.8", "1720475015072", "+33 1 5555312", "lucas.dubois@example.fr"),
    P("p-005", "García",   ["Sofía"],     "female", "1990-09-21", "ES", "Madrid",     "28013", "Gran Vía 28",        "es-ES",
      "urn:oid:1.3.6.1.4.1.19126.3", "12345678Z", "+34 91 5550401", "sofia.garcia@example.es"),
    P("p-006", "Silva",    ["João"],      "male",   "1945-12-05", "PT", "Lisbon",     "1100-148", "Rua Augusta 50",  "pt-PT",
      "urn:oid:2.16.620.1.1.2.3", "123456789", "+351 21 5550533", "joao.silva@example.pt"),
    P("p-007", "Jansen",   ["Emma"],      "female", "2003-06-30", "NL", "Amsterdam",  "1012", "Damrak 70",          "nl-NL",
      "urn:oid:2.16.840.1.113883.2.4.6.3", "108457392", "+31 20 5550604", "emma.jansen@example.nl"),
    P("p-008", "Kowalski", ["Piotr"],     "male",   "1962-02-09", "PL", "Warsaw",     "00-001", "ul. Marszałkowska 1","pl-PL",
      "urn:oid:2.16.616.1.113883.2.7.3.1", "62020912345", "+48 22 5550712", "piotr.kowalski@example.pl"),
    P("p-009", "Lindberg", ["Astrid"],    "female", "1978-08-11", "SE", "Stockholm",  "111 20", "Drottninggatan 71", "sv-SE",
      "urn:oid:1.2.752.129.2.1.3.1", "19780811-1234", "+46 8 5550821", "astrid.lindberg@example.se"),
    P("p-010", "Virtanen", ["Mikko"],     "male",   "1959-01-26", "FI", "Helsinki",   "00100", "Esplanadi 12",       "fi-FI",
      "urn:oid:1.2.246.21", "260159-345A", "+358 9 5550930", "mikko.virtanen@example.fi"),
]


def _patient(p: P) -> dict:
    return {
        "resourceType": "Patient",
        "id": p.pid,
        "meta": {"profile": ["http://hl7.eu/fhir/StructureDefinition/Patient-eu"]},
        "identifier": [{
            "system": p.national_id_system,
            "value": p.national_id,
            "use": "official",
        }],
        "active": True,
        "name": [{"use": "official", "family": p.family, "given": p.given}],
        "telecom": [
            {"system": "phone", "value": p.phone, "use": "home"},
            {"system": "email", "value": p.email},
        ],
        "gender": p.gender,
        "birthDate": p.birthdate,
        "address": [{
            "use": "home",
            "line": [p.line],
            "city": p.city,
            "postalCode": p.postal,
            "country": p.country,
        }],
        "communication": [{"language": {"coding": [{"system": "urn:ietf:bcp:47", "code": p.language}]}}],
    }


def _practitioner(idx: int) -> dict:
    return {
        "resourceType": "Practitioner",
        "id": f"pract-{idx:03d}",
        "identifier": [{"system": "urn:oid:1.2.3.4.5", "value": f"DOC-{idx:05d}"}],
        "name": [{"family": ["Hoffmann", "Bianchi", "Bernard", "Lopez", "Petrov"][idx % 5],
                  "given": [["Eva", "Marco", "Pierre", "Carmen", "Ivan"][idx % 5]],
                  "prefix": ["Dr."]}],
        "telecom": [{"system": "phone", "value": "+49 30 99999"}],
    }


def _organization(idx: int) -> dict:
    return {
        "resourceType": "Organization",
        "id": f"org-{idx:03d}",
        "identifier": [{"system": "urn:oid:1.2.3.4.5", "value": f"ORG-{idx:04d}"}],
        "name": ["EHDS Demo University Hospital", "Demo General Hospital",
                 "Demo Regional Clinic"][idx % 3],
        "telecom": [{"system": "phone", "value": "+49 30 11111"}],
        "address": [{"city": "Berlin", "country": "DE"}],
    }


def _allergy(p: P, idx: int) -> dict:
    substances = [
        ("227493005", "Cashew nuts", "food"),
        ("256277009", "Grass pollen", "environment"),
        ("372687004", "Amoxicillin", "medication"),
    ]
    code, disp, cat = substances[idx % 3]
    return {
        "resourceType": "AllergyIntolerance",
        "id": f"allergy-{p.pid}-{idx:02d}",
        "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical", "code": "active"}]},
        "verificationStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification", "code": "confirmed"}]},
        "category": [cat],
        "criticality": "low",
        "code": {"coding": [{"system": "http://snomed.info/sct", "code": code, "display": disp}], "text": disp},
        "patient": {"reference": f"Patient/{p.pid}"},
    }


def _condition(p: P, idx: int) -> dict:
    cs = [
        ("38341003",  "Hypertensive disorder"),
        ("44054006",  "Diabetes mellitus type 2"),
        ("195967001", "Asthma"),
        ("13645005",  "Chronic obstructive lung disease"),
        ("709044004", "Chronic kidney disease"),
    ]
    code, disp = cs[idx % 5]
    return {
        "resourceType": "Condition",
        "id": f"cond-{p.pid}-{idx:02d}",
        "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]},
        "verificationStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-ver-status", "code": "confirmed"}]},
        "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-category", "code": "problem-list-item"}]}],
        "code": {"coding": [{"system": "http://snomed.info/sct", "code": code, "display": disp}], "text": disp},
        "subject": {"reference": f"Patient/{p.pid}"},
        "recordedDate": "2023-04-15",
    }


def _medication(p: P, idx: int) -> dict:
    # RxNorm (RXCUI) codes — well-known, publicly resolvable, no licence
    # restriction. coding[].display strings MUST match the canonical RxNorm
    # text exactly (the HL7 validator cross-checks against tx.fhir.org). The
    # human-friendly label lives in code.text.
    meds = [
        ("314076", "Lisinopril 10 MG Oral Tablet",                   "Lisinopril 10 mg tablet"),
        ("860974", "metformin hydrochloride 500 MG",                 "Metformin HCl 500 mg"),
        ("630208", "Albuterol 0.83 MG/ML Inhalation Solution",       "Albuterol 0.83 mg/mL inhalation solution"),
        ("617312", "atorvastatin 10 MG Oral Tablet",                 "Atorvastatin 10 mg tablet"),
        ("197361", "Amlodipine 5 MG Oral Tablet",                    "Amlodipine 5 mg tablet"),
    ]
    code, disp, text = meds[idx % 5]
    return {
        "resourceType": "Medication",
        "id": f"med-{p.pid}-{idx:02d}",
        "code": {"coding": [{"system": "http://www.nlm.nih.gov/research/umls/rxnorm", "code": code, "display": disp}], "text": text},
    }


def _med_statement(p: P, idx: int, med_ref: str) -> dict:
    return {
        "resourceType": "MedicationStatement",
        "id": f"medst-{p.pid}-{idx:02d}",
        "status": "active",
        "medicationReference": {"reference": med_ref},
        "subject": {"reference": f"Patient/{p.pid}"},
        "effectiveDateTime": "2023-01-01",
        "dosage": [{"text": "1 tablet daily by mouth"}],
    }


def _med_request(p: P, idx: int, med_ref: str) -> dict:
    return {
        "resourceType": "MedicationRequest",
        "id": f"medrq-{p.pid}-{idx:02d}",
        "status": "active",
        "intent": "order",
        "medicationReference": {"reference": med_ref},
        "subject": {"reference": f"Patient/{p.pid}"},
        "authoredOn": "2024-02-10",
        "dosageInstruction": [{"text": "1 tablet daily by mouth"}],
    }


def _med_dispense(p: P, idx: int, med_ref: str) -> dict:
    return {
        "resourceType": "MedicationDispense",
        "id": f"meddi-{p.pid}-{idx:02d}",
        "status": "completed",
        "medicationReference": {"reference": med_ref},
        "subject": {"reference": f"Patient/{p.pid}"},
        "whenHandedOver": "2024-02-12",
        "quantity": {"value": 30, "unit": "tablet"},
    }


def _immunization(p: P, idx: int) -> dict:
    # CVX display strings must match CDC IIS canonical text or the HL7
    # validator emits Wrong-Display-Name errors against the terminology server.
    vaxes = [
        ("207", "COVID-19, mRNA, LNP-S, PF, 100 mcg/0.5mL dose or 50 mcg/0.25mL dose"),
        ("140", "Influenza, split virus, trivalent, PF"),
        ("115", "Tdap"),
        ("83",  "Hep A, ped/adol, 2 dose"),
    ]
    code, disp = vaxes[idx % 4]
    return {
        "resourceType": "Immunization",
        "id": f"imm-{p.pid}-{idx:02d}",
        "status": "completed",
        "vaccineCode": {"coding": [{"system": "http://hl7.org/fhir/sid/cvx", "code": code, "display": disp}], "text": disp},
        "patient": {"reference": f"Patient/{p.pid}"},
        "occurrenceDateTime": f"2023-{(idx % 12) + 1:02d}-15",
    }


def _procedure(p: P, idx: int) -> dict:
    procs = [
        ("80146002",   "Appendectomy"),
        ("103693007",  "Diagnostic procedure"),
        ("387713003",  "Surgical procedure"),
    ]
    code, disp = procs[idx % 3]
    return {
        "resourceType": "Procedure",
        "id": f"proc-{p.pid}-{idx:02d}",
        "status": "completed",
        "code": {"coding": [{"system": "http://snomed.info/sct", "code": code, "display": disp}], "text": disp},
        "subject": {"reference": f"Patient/{p.pid}"},
        "performedDateTime": "2023-08-22",
    }


def _bp_panel(p: P, idx: int) -> dict:
    """proper R4 BP panel (LOINC 85354-9) with systolic+diastolic components.

    Using standalone 8480-6 triggers the http://hl7.org/fhir/StructureDefinition/bp
    profile, which requires the panel layout with both components. So we emit the
    panel directly instead.
    """
    return {
        "resourceType": "Observation",
        "id": f"obs-{p.pid}-{idx:02d}",
        "status": "final",
        "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "vital-signs"}]}],
        "code": {"coding": [{"system": "http://loinc.org", "code": "85354-9", "display": "Blood pressure panel with all children optional"}], "text": "Blood pressure"},
        "subject": {"reference": f"Patient/{p.pid}"},
        "effectiveDateTime": "2024-03-01",
        "component": [
            {
                "code": {"coding": [{"system": "http://loinc.org", "code": "8480-6", "display": "Systolic blood pressure"}]},
                "valueQuantity": {"value": 120 + idx, "unit": "mm[Hg]", "system": "http://unitsofmeasure.org", "code": "mm[Hg]"},
            },
            {
                "code": {"coding": [{"system": "http://loinc.org", "code": "8462-4", "display": "Diastolic blood pressure"}]},
                "valueQuantity": {"value": 80 + idx, "unit": "mm[Hg]", "system": "http://unitsofmeasure.org", "code": "mm[Hg]"},
            },
        ],
    }


def _observation(p: P, idx: int) -> dict:
    # LOINC display strings must match the official en-US text (validator
    # cross-checks the LOINC terminology server). One observation per patient
    # (idx == 0) is rendered as a proper BP panel via _bp_panel().
    if idx == 0:
        return _bp_panel(p, idx)

    obs_specs = [
        # (loinc, display, unit, value, category) — 9 standalone observations
        ("8867-4",  "Heart rate",                                      "/min",   72 + idx, "vital-signs"),
        ("8310-5",  "Body temperature",                                "Cel",    36 + (idx % 2), "vital-signs"),
        ("9279-1",  "Respiratory rate",                                "/min",   16 + (idx % 4), "vital-signs"),
        # 2708-6 is the "magic" LOINC the oxygensat base profile requires.
        # 59408-5 (pulse-oximetry variant) trips OxygenSatCode-1 invariant.
        ("2708-6",  "Oxygen saturation in Arterial blood",            "%",      96 + (idx % 4), "vital-signs"),
        ("2339-0",  "Glucose [Mass/volume] in Blood",                  "mg/dL",  90 + idx * 2, "laboratory"),
        ("2093-3",  "Cholesterol [Mass/volume] in Serum or Plasma",    "mg/dL",  180 + idx,    "laboratory"),
        ("4548-4",  "Hemoglobin A1c/Hemoglobin.total in Blood",        "%",      5.6 + (idx % 5) * 0.1, "laboratory"),
        ("718-7",   "Hemoglobin [Mass/volume] in Blood",               "g/dL",   13.0 + (idx % 3) * 0.1, "laboratory"),
        ("2160-0",  "Creatinine [Mass/volume] in Serum or Plasma",     "mg/dL",  0.9 + (idx % 3) * 0.1, "laboratory"),
    ]
    code, disp, unit, val, cat = obs_specs[(idx - 1) % len(obs_specs)]
    return {
        "resourceType": "Observation",
        "id": f"obs-{p.pid}-{idx:02d}",
        "status": "final",
        "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": cat}]}],
        "code": {"coding": [{"system": "http://loinc.org", "code": code, "display": disp}], "text": disp},
        "subject": {"reference": f"Patient/{p.pid}"},
        "effectiveDateTime": "2024-03-01",
        "valueQuantity": {"value": val, "unit": unit, "system": "http://unitsofmeasure.org", "code": unit},
    }


def _encounter(p: P, idx: int) -> dict:
    return {
        "resourceType": "Encounter",
        "id": f"enc-{p.pid}-{idx:02d}",
        "status": "finished",
        "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                  "code": "IMP" if idx == 0 else "AMB",
                  "display": "inpatient encounter" if idx == 0 else "ambulatory"},
        "subject": {"reference": f"Patient/{p.pid}"},
        "period": {"start": "2024-04-10T09:00:00+02:00", "end": "2024-04-12T16:00:00+02:00"},
        "reasonCode": [{"text": "Post-operative recovery" if idx == 0 else "Routine check-up"}],
    }


def _specimen(p: P, idx: int) -> dict:
    types = [
        ("119297000", "Blood specimen"),
        ("119295008", "Specimen obtained by aspiration"),
        ("122575003", "Urine specimen"),
    ]
    code, disp = types[idx % 3]
    return {
        "resourceType": "Specimen",
        "id": f"spec-{p.pid}-{idx:02d}",
        "status": "available",
        "type": {"coding": [{"system": "http://snomed.info/sct", "code": code, "display": disp}], "text": disp},
        "subject": {"reference": f"Patient/{p.pid}"},
        "collection": {"collectedDateTime": "2024-03-01T08:30:00+01:00"},
    }


def _diag_lab(p: P, obs_refs: list[str], specimen_refs: list[str], practitioner_ref: str, idx: int) -> dict:
    return {
        "resourceType": "DiagnosticReport",
        "id": f"dr-lab-{p.pid}-{idx:02d}",
        "status": "final",
        "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0074", "code": "LAB", "display": "Laboratory"}]}],
        "code": {"coding": [{"system": "http://loinc.org", "code": "11502-2", "display": "Laboratory report"}]},
        "subject": {"reference": f"Patient/{p.pid}"},
        "effectiveDateTime": "2024-03-01",
        "issued": "2024-03-01T12:00:00+01:00",
        "performer": [{"reference": practitioner_ref}],
        "specimen": [{"reference": r} for r in specimen_refs],
        "result": [{"reference": r} for r in obs_refs],
    }


def _diag_rad(p: P, imaging_ref: str, practitioner_ref: str) -> dict:
    return {
        "resourceType": "DiagnosticReport",
        "id": f"dr-rad-{p.pid}",
        "status": "final",
        "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0074", "code": "RAD", "display": "Radiology"}]}],
        "code": {"coding": [{"system": "http://loinc.org", "code": "30746-2", "display": "Portable XR Chest Views"}], "text": "Chest X-ray report"},
        "subject": {"reference": f"Patient/{p.pid}"},
        "effectiveDateTime": "2024-04-11",
        "issued": "2024-04-11T15:00:00+02:00",
        "performer": [{"reference": practitioner_ref}],
        "imagingStudy": [{"reference": imaging_ref}],
        "conclusion": "Normal chest radiograph.",
    }


def _imaging_study(p: P) -> dict:
    return {
        "resourceType": "ImagingStudy",
        "id": f"img-{p.pid}",
        "status": "available",
        "subject": {"reference": f"Patient/{p.pid}"},
        "started": "2024-04-11T14:30:00+02:00",
        "numberOfSeries": 1,
        "numberOfInstances": 2,
        "modality": [{"system": "http://dicom.nema.org/resources/ontology/DCM", "code": "CR", "display": "Computed Radiography"}],
        "series": [{
            "uid": "1.2.840.113619.2.55.3.604688119.971.1392812862." + p.pid,
            "number": 1,
            "modality": {"system": "http://dicom.nema.org/resources/ontology/DCM", "code": "CR"},
            "numberOfInstances": 2,
            "bodySite": {"system": "http://snomed.info/sct", "code": "51185008", "display": "Thoracic structure"},
        }],
    }


def _docref(p: P, category: str) -> dict:
    type_coding = DOC_TYPES[category]
    cat_coding = CATEGORY_CODES[category]
    binary_id = f"doc-{p.pid}-{category}"
    return {
        "resourceType": "DocumentReference",
        "id": f"dr-{p.pid}-{category}",
        "meta": {"profile": [EHDS_DOCREF_PROFILE]},
        "status": "current",
        "type": {"coding": [type_coding]},
        "category": [{"coding": [cat_coding]}],
        "subject": {"reference": f"Patient/{p.pid}"},
        "date": "2024-04-15T10:00:00+02:00",
        "description": f"{cat_coding['display']} for {p.given[0]} {p.family}",
        "content": [{
            "attachment": {
                "contentType": "application/fhir+json",
                "url": f"Binary/{binary_id}",
            },
            "format": {"system": "http://ihe.net/fhir/ValueSet/IHE.FormatCode.codesystem", "code": "urn:ihe:iti:xds-sd:text:2008"},
        }],
    }


def _ensure_dirs(base: Path) -> None:
    for d in [
        "patients", "observations", "medication-statements", "medication-dispenses",
        "medication-requests", "medications", "conditions", "allergy-intolerances",
        "immunizations", "procedures", "diagnostic-reports", "imaging-studies",
        "encounters", "specimens", "practitioners", "organizations",
        "compositions", "document-references", "inbox",
    ]:
        (base / d).mkdir(parents=True, exist_ok=True)


def _w(base: Path, sub: str, res: dict) -> None:
    (base / sub / f"{res['id']}.json").write_text(json.dumps(res, indent=2, sort_keys=True))


def seed(base: Path, clean: bool = False) -> None:
    if clean and base.exists():
        for d in base.iterdir():
            if d.is_dir():
                shutil.rmtree(d)
    _ensure_dirs(base)
    random.seed(42)

    # one practitioner + one organization per patient for variety
    for i, p in enumerate(PANEL):
        pract = _practitioner(i + 1)
        org = _organization(i + 1)
        _w(base, "practitioners", pract)
        _w(base, "organizations", org)
        _w(base, "patients", _patient(p))

        # allergies (3)
        for k in range(3):
            _w(base, "allergy-intolerances", _allergy(p, k))
        # conditions (5)
        for k in range(5):
            _w(base, "conditions", _condition(p, k))
        # medications + statements + requests + dispenses (5/5/5/3)
        med_refs = []
        for k in range(5):
            m = _medication(p, k)
            med_refs.append(f"Medication/{m['id']}")
            _w(base, "medications", m)
        for k in range(5):
            _w(base, "medication-statements", _med_statement(p, k, med_refs[k]))
            _w(base, "medication-requests", _med_request(p, k, med_refs[k]))
        for k in range(3):
            _w(base, "medication-dispenses", _med_dispense(p, k, med_refs[k]))
        # immunizations (4)
        for k in range(4):
            _w(base, "immunizations", _immunization(p, k))
        # procedures (3)
        for k in range(3):
            _w(base, "procedures", _procedure(p, k))
        # observations (10)
        obs_ids = []
        for k in range(10):
            o = _observation(p, k)
            obs_ids.append(o["id"])
            _w(base, "observations", o)
        # specimens (3)
        spec_ids = []
        for k in range(3):
            s = _specimen(p, k)
            spec_ids.append(s["id"])
            _w(base, "specimens", s)
        # encounters (2)
        for k in range(2):
            _w(base, "encounters", _encounter(p, k))
        # imaging study + radiology dx report
        img = _imaging_study(p)
        _w(base, "imaging-studies", img)
        _w(base, "diagnostic-reports", _diag_rad(p, f"ImagingStudy/{img['id']}", f"Practitioner/{pract['id']}"))
        # 2 lab dx reports (each referencing the lab observations + specimens)
        lab_obs_refs = [f"Observation/{oid}" for oid in obs_ids if "obs" in oid]
        for k in range(2):
            chunk = lab_obs_refs[k::2]
            sp_refs = [f"Specimen/{spec_ids[k % len(spec_ids)]}"]
            _w(base, "diagnostic-reports", _diag_lab(p, chunk, sp_refs, f"Practitioner/{pract['id']}", k))

        # one DocumentReference per priority category
        for category in DOC_TYPES.keys():
            _w(base, "document-references", _docref(p, category))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    ap.add_argument("--clean", action="store_true")
    args = ap.parse_args()
    seed(Path(args.data_dir), clean=args.clean)
    print(f"seeded -> {args.data_dir}")


if __name__ == "__main__":
    main()
