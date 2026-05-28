"""Orchestrate fetch -> transform -> store-write for one Epic patient.

Usage (programmatic):

    from app.sources.epic_client import EpicClient
    from app.sources.epic_ingest import ingest_patient

    summary = ingest_patient(EpicClient(), "erXuFYUfucBZaryVksYEcMg3")
    # summary.patient_id  ->  local uuid
    # summary.counts      ->  {"Condition": 5, "Observation": 23, ...}

After ingest, the existing IPS bundler picks it up:

    from app.fhir.document import compile_document
    bundle = compile_document(summary.patient_id, "patient-summary")
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from app.fhir import store
from app.sources.epic_client import EpicClient
from app.sources.epic_transform import (
    SUPPORTED,
    absent_allergy,
    absent_medication,
    absent_problem,
    transform_bundle,
)

# Per-section search params we hit on Epic. Keep this list tight — the demo's
# Bundle compiler only consumes the resource types in app.fhir.store.SUPPORTED_TYPES.
# search params here follow Epic's documented US-Core/SMART search support.
FETCH_PLAN: list[tuple[str, dict[str, Any]]] = [
    ("AllergyIntolerance", {}),
    ("Condition", {"category": "problem-list-item"}),
    ("Condition", {"category": "encounter-diagnosis"}),
    ("MedicationRequest", {}),
    ("MedicationStatement", {}),
    ("Immunization", {}),
    ("Procedure", {}),
    ("Observation", {"category": "laboratory"}),
    ("Observation", {"category": "vital-signs"}),
    ("Observation", {"category": "social-history"}),
    ("DiagnosticReport", {}),
    ("Encounter", {}),
]


@dataclass(frozen=True)
class IngestSummary:
    epic_patient_id: str
    patient_id: str
    counts: dict[str, int]
    skipped: list[str]


def _seen_key(r: dict[str, Any]) -> str:
    return f"{r.get('resourceType')}/{r.get('id')}"


def ingest_patient(
    client: EpicClient,
    epic_patient_id: str,
    *,
    dry_run: bool = False,
) -> IngestSummary:
    # 1. fetch
    fetched: list[dict[str, Any]] = []
    seen: set[str] = set()

    # the Patient itself
    pat = client.read("Patient", epic_patient_id)
    fetched.append(pat)
    seen.add(_seen_key(pat))

    # compartment resources
    for rtype, extra in FETCH_PLAN:
        params = {"patient": epic_patient_id, **extra}
        try:
            for r in client.search(rtype, params=params):
                key = _seen_key(r)
                if key in seen:
                    continue
                if r.get("resourceType") not in SUPPORTED:
                    continue
                seen.add(key)
                fetched.append(r)
        except Exception as exc:  # noqa: BLE001
            # one-off resource type failure shouldn't kill the whole ingest;
            # log via raise-and-collect would be nicer but we keep it simple here.
            print(f"  ! {rtype} search failed: {exc}")

    # 2. transform
    locals_, _id_map, patient_local_id = transform_bundle(fetched)
    if patient_local_id is None:
        raise RuntimeError("Patient resource missing from fetched bundle")
    patient_ref = f"Patient/{patient_local_id}"

    # 3. fill IPS-required sections with absent-data placeholders if empty
    have_allergy = any(r["resourceType"] == "AllergyIntolerance" for r in locals_)
    have_problem = any(
        r["resourceType"] == "Condition"
        and any(c.get("code") == "problem-list-item"
                for cc in (r.get("category") or []) for c in (cc.get("coding") or []))
        for r in locals_
    )
    have_med = any(r["resourceType"] == "MedicationStatement" for r in locals_)
    if not have_allergy:
        locals_.append(absent_allergy(patient_ref))
    if not have_problem:
        locals_.append(absent_problem(patient_ref))
    if not have_med:
        locals_.append(absent_medication(patient_ref))

    # 4. write
    skipped: list[str] = []
    counts: Counter[str] = Counter()
    if not dry_run:
        for r in locals_:
            try:
                store.write(r)
            except KeyError:
                # resource type not in the store's _TYPE_TO_DIR (e.g. Encounter
                # is supported, Specimen too, but Device isn't); skip quietly.
                skipped.append(_seen_key(r))
                continue
            counts[r["resourceType"]] += 1

    return IngestSummary(
        epic_patient_id=epic_patient_id,
        patient_id=patient_local_id,
        counts=dict(counts),
        skipped=skipped,
    )
