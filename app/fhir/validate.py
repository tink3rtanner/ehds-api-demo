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
    """run fhir.resources pydantic validation; return (ok, [problem strings]).

    fhir.resources 8.x exposes get_fhir_model_class at the top level; older
    versions used construct_fhir_element or fhirtypesvalidators.get_fhir_model_class.
    We try the canonical 8.x path first and fall back to the older shapes so
    this works across version bumps.
    """
    rtype = resource.get("resourceType")
    if not rtype:
        return False, ["resource has no resourceType"]

    # fhir.resources 8.x defaults to R5 models at the top level; we explicitly
    # use R4B (the R4 maintenance release) which matches our server's
    # advertised fhirVersion 4.0.1. Without R4B the validator treats fields
    # like Composition.subject as 0..* lists per R5 and rejects valid R4
    # bundles where it's a single Reference.
    try:
        from fhir.resources.R4B import get_fhir_model_class  # type: ignore
    except ImportError:
        get_fhir_model_class = None  # type: ignore

    # fallbacks for older fhir.resources installs
    if get_fhir_model_class is None:
        try:
            from fhir.resources.fhirtypesvalidators import (  # type: ignore
                get_fhir_model_class as _get,
            )
            get_fhir_model_class = _get
        except ImportError:
            pass

    if get_fhir_model_class is not None:
        try:
            cls = get_fhir_model_class(rtype)
        except (KeyError, LookupError, AttributeError) as e:
            return False, [f"unknown resourceType {rtype!r}: {e}"]
        try:
            cls.model_validate(resource)
            return True, []
        except Exception as e:  # noqa: BLE001  pydantic raises ValidationError
            # surface a useful one-line summary instead of dumping the whole tree
            msg = str(e).replace("\n", " ").strip()
            return False, [msg[:600]]

    # last-resort fallback: 6.x and earlier
    try:
        from fhir.resources import construct_fhir_element  # type: ignore
        construct_fhir_element(rtype, resource)  # type: ignore
        return True, []
    except ImportError as e:
        return True, [f"validator-unavailable: {e}"]
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
