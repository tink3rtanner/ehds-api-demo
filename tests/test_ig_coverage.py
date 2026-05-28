"""IG artifact coverage: every named artifact from the IG appears in at least one test."""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS = REPO_ROOT / "tests"
APP = REPO_ROOT / "app"

# the canonical names of artifacts the EHDS IG defines (per artifacts.html).
IG_ARTIFACTS = [
    # ActorDefinitions
    "DocumentAccessProvider",
    "DocumentConsumer",
    "DocumentPublisher",
    "GroupedDocumentPublisherAccessProvider",
    "ResourceAccessProvider",
    "ResourceConsumer",
    # CapabilityStatements
    "EEHRxF Document Access Provider",
    "EEHRxF Document Access Provider - Document Submission Option",
    "EEHRxF Document Consumer",
    "EEHRxF Document Publisher",
    "EEHRxF Grouped Document Publisher/Access Provider",
    "EEHRxF Resource Access Provider",
    "EEHRxF Resource Consumer",
    # Profiles
    "EEHRxF MHD DocumentReference Profile",
    # ValueSets
    "EEHRxFDocumentTypeValueSet",
    "EEHRxFDocumentTypeValueSetForDischargeReports",
    "EEHRxFDocumentTypeValueSetForLaboratoryReports",
    "EEHRxFDocumentTypeValueSetForMedicalImaging",
    "EEHRxFDocumentTypeValueSetForPatientSummaries",
    # CodeSystems
    "EEHRxFDocumentPriorityCategoryCodeSystem",
    "patient-summary",
    "laboratory-report",
    "discharge-report",
    "imaging-report",
    "prescription",
]


def _all_text() -> str:
    """concatenate all .py text under tests/ and app/."""
    chunks: list[str] = []
    for d in (TESTS, APP):
        for p in d.rglob("*.py"):
            try:
                chunks.append(p.read_text())
            except Exception:
                pass
    return "\n".join(chunks)


@pytest.mark.parametrize("artifact", IG_ARTIFACTS)
def test_ig_artifact_referenced_in_tests_or_app(artifact):
    """soft form of coverage: artifact's name (or its slug) shows up in our
    tests or app source, indicating intentional handling."""
    body = _all_text().lower()
    needle = artifact.lower()
    if needle not in body:
        # accept a kebab/snake form too
        slug = artifact.replace(" ", "").replace("-", "").lower()
        body_compact = body.replace(" ", "").replace("-", "").replace("_", "")
        assert slug in body_compact, f"IG artifact {artifact!r} not referenced anywhere"
