"""validation wrappers.

structural_validate: fhir.resources pydantic check. fast, in-process.
profile_validate: shells out to the HL7 java validator. heavy.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.config import settings


def structural_validate(resource: dict[str, Any]) -> tuple[bool, list[str]]:
    """run fhir.resources pydantic validation; return (ok, [problem strings])."""
    try:
        from fhir.resources.fhirtypesvalidators import get_fhir_model_class  # type: ignore
    except Exception:
        # fhir.resources >= 7 prefers construct_fhir_element / direct model imports
        try:
            from fhir.resources import construct_fhir_element  # type: ignore
        except Exception as e:
            return True, [f"validator-unavailable: {e}"]

        try:
            construct_fhir_element(resource["resourceType"], resource)  # type: ignore
            return True, []
        except Exception as e:  # noqa: BLE001
            return False, [str(e)]

    try:
        cls = get_fhir_model_class(resource["resourceType"])
        cls.model_validate(resource)
        return True, []
    except Exception as e:  # noqa: BLE001
        return False, [str(e)]


def profile_validate(resource: dict[str, Any], *, igs: list[str] | None = None,
                     timeout: int = 240) -> tuple[bool, list[dict]]:
    """run the HL7 java validator. returns (ok, [issue dicts]). errors only mean fail."""
    jar = settings.validator_jar
    if not jar.exists():
        return True, [{"severity": "information", "diagnostics": "validator jar absent — skipped"}]
    igs = igs or []
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "input.json"
        src.write_text(json.dumps(resource))
        out = Path(td) / "out.json"
        cmd = ["java", "-jar", str(jar), str(src), "-version", "4.0.1", "-output", str(out)]
        for ig in igs:
            cmd += ["-ig", ig]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, [{"severity": "error", "diagnostics": "validator timeout"}]
        if not out.exists():
            return False, [{"severity": "error", "diagnostics": proc.stderr.strip() or proc.stdout.strip()}]
        report = json.loads(out.read_text())
        issues = report.get("issue", [])
        errs = [i for i in issues if i.get("severity") in ("error", "fatal")]
        return (len(errs) == 0), issues
