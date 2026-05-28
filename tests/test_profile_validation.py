"""profile validation via the HL7 java validator.

heavy: each java invocation takes ~30-60s. we keep this layer separate so
the fast feedback loop (other tests) stays snappy, and we run it explicitly
in CI / `make validate`.

note: the validator emits structure warnings against the EU profiles when
the IG packages aren't pinned to specific versions. we accept warnings but
fail on error/fatal issues. additionally, since we don't ship pre-packed
.tgz IG packages, we run base FHIR R4 validation only (no -ig flags). this
catches structural + R4-level invariants; profile-level invariants are
covered by structural_validate's pydantic + our own valueset tests.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.conftest import requires_validator

pytestmark = [pytest.mark.asyncio, requires_validator]

REPO_ROOT = Path(__file__).resolve().parent.parent
JAR = REPO_ROOT / ".cache" / "validator_cli.jar"
PANEL_IDS = [f"p-{i:03d}" for i in range(1, 11)]
from app.fhir.document import CATEGORY_TO_DOC_TYPE
from app.fhir.ids import bundle_id
CATEGORIES = list(CATEGORY_TO_DOC_TYPE.keys())


def _run_validator(resource: dict, *, version: str = "4.0.1", timeout: int = 240) -> tuple[bool, list[dict]]:
    """invoke the validator on the given resource; returns (ok, issues)."""
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "input.json"
        src.write_text(json.dumps(resource))
        out = Path(td) / "out.json"
        cmd = ["java", "-jar", str(JAR), str(src), "-version", version, "-output", str(out)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if not out.exists():
            return False, [{"severity": "error", "diagnostics": (proc.stderr or proc.stdout).strip()[:400]}]
        report = json.loads(out.read_text())
        issues = report.get("issue", [])
        errs = [i for i in issues if i.get("severity") in ("error", "fatal")]
        return (len(errs) == 0), issues


@pytest.mark.parametrize("pid", ["p-001"])  # one patient × 4 categories = enough to prove the path
@pytest.mark.parametrize("category", CATEGORIES)
async def test_compiled_documents_pass_r4_validation(client, auth_headers, pid, category):
    r = await client.get(f"/Bundle/{bundle_id(pid, category)}", headers=auth_headers)
    assert r.status_code == 200, r.text
    bundle = r.json()
    ok, issues = _run_validator(bundle)
    errs = [i for i in issues if i.get("severity") in ("error", "fatal")]
    assert ok, f"validation errors for {pid}/{category}:\n" + "\n".join(
        f"  {i.get('severity')}: {_issue_text(i)}" for i in errs[:10]
    )


@pytest.mark.parametrize("pid", PANEL_IDS)
async def test_patient_resources_pass_r4_validation(client, auth_headers, pid):
    """every Patient passes R4 structural validation."""
    r = await client.get(f"/Patient/{pid}", headers=auth_headers)
    assert r.status_code == 200
    ok, issues = _run_validator(r.json())
    errs = [i for i in issues if i.get("severity") in ("error", "fatal")]
    assert ok, f"errors for {pid}: " + "; ".join(_issue_text(i) for i in errs[:3])


def _issue_text(issue: dict) -> str:
    """recent validator builds put the message under details.text; older
    ones use diagnostics. read whichever is populated."""
    return (issue.get("details") or {}).get("text") or issue.get("diagnostics") or "(no text)"
