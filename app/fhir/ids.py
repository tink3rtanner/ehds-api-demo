"""Deterministic UUID generation for every synthetic-demo resource.

Real FHIR servers (Epic, HAPI, Smile, etc.) mint UUIDs as resource ids. We
do the same — but deterministically (uuid5 over a project namespace + a
canonical-path string) so tests stay golden across reseed.

Every resource id in the seed data goes through one of these helpers. The
hand-rolled slot tags (``p-001``, ``obs-p-001-00``) are gone everywhere
except as the *slot identifier* on Patient (Patient.identifier with system
``urn:ehds-demo:slot``) — that's how PDQm searches by ``identifier=p-001``
still find the right patient.
"""
from __future__ import annotations

import uuid

# project-scoped namespace; uuid5() over this is stable across all hosts.
EHDS_NAMESPACE = uuid.UUID("9a3c7c3f-43a1-58a7-89f9-3ea8f1486d6b")

SLOT_IDENTIFIER_SYSTEM = "urn:ehds-demo:slot"


def _u5(path: str) -> str:
    return str(uuid.uuid5(EHDS_NAMESPACE, path))


# ---------- per-resource-type helpers (the only place hand-rolled paths live) ----------

def patient_id(slot: str) -> str:
    """deterministic Patient.id for slot ``p-001`` etc."""
    return _u5(f"Patient/{slot}")


def practitioner_id(slot: str) -> str:
    return _u5(f"Practitioner/{slot}")


def organization_id(slot: str) -> str:
    return _u5(f"Organization/{slot}")


def child_id(patient_slot: str, resource_type: str, index: int) -> str:
    """deterministic id for a clinical resource owned by a patient slot.

    Used for AllergyIntolerance, Condition, Medication, MedicationStatement,
    MedicationRequest, MedicationDispense, Immunization, Procedure,
    Observation, Specimen, Encounter, ImagingStudy, DiagnosticReport, …
    """
    return _u5(f"{resource_type}/{patient_slot}/{index}")


# ---------- document-side helpers (already used in earlier refactor) ----------

def bundle_id(patient_id_or_slot: str, category: str) -> str:
    """deterministic id for the compiled FHIR document Bundle.

    accepts either a slot label (``p-001``) or a Patient.id (uuid) so
    test helpers can look up either way deterministically.
    """
    return _u5(f"Bundle/{patient_id_or_slot}/{category}")


def docref_id(patient_id_or_slot: str, category: str) -> str:
    return _u5(f"DocumentReference/{patient_id_or_slot}/{category}")


def bundle_identifier(patient_id_or_slot: str, category: str) -> str:
    return f"urn:uuid:{_u5(f'identifier/Bundle/{patient_id_or_slot}/{category}')}"


def composition_id(patient_id_or_slot: str, category: str) -> str:
    return _u5(f"Composition/{patient_id_or_slot}/{category}")
