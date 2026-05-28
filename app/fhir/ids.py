"""Deterministic UUID generation for synthetic-demo resources.

Real FHIR servers (Epic, HAPI, Smile, etc.) typically use UUIDs as resource
ids. Our synthetic panel previously used hand-rolled ids like
``doc-p-001-patient-summary`` which looked obviously fake. This module
mints stable uuid5 values from canonical-path strings so tests stay
deterministic across reseed.

The namespace is a constant per project — pick a new one if you ever fork
and want non-colliding ids.
"""
from __future__ import annotations

import uuid

# project-scoped namespace; uuid5() over this is stable across all hosts.
# generated once with uuid5(NAMESPACE_URL, 'ehds-api-demo:2026-05-28')
EHDS_NAMESPACE = uuid.UUID("9a3c7c3f-43a1-58a7-89f9-3ea8f1486d6b")


def _u5(path: str) -> str:
    return str(uuid.uuid5(EHDS_NAMESPACE, path))


def bundle_id(patient_id: str, category: str) -> str:
    """deterministic id for the compiled FHIR document Bundle."""
    return _u5(f"Bundle/{patient_id}/{category}")


def docref_id(patient_id: str, category: str) -> str:
    """deterministic id for the DocumentReference pointing at the Bundle."""
    return _u5(f"DocumentReference/{patient_id}/{category}")


def bundle_identifier(patient_id: str, category: str) -> str:
    """deterministic urn:uuid for Bundle.identifier.value."""
    return f"urn:uuid:{_u5(f'identifier/Bundle/{patient_id}/{category}')}"


def composition_id(patient_id: str, category: str) -> str:
    """deterministic id for the synthesised Composition."""
    return _u5(f"Composition/{patient_id}/{category}")
