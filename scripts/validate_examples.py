"""Validate the six priority-category examples with the HL7 java validator.

Four documents (compiled for the reference patient) against their EU Bundle
profile, plus one MedicationRequest and one MedicationDispense against the MPD
resource profiles. Prints one line per example and exits 1 if any has errors.

    python -m scripts.validate_examples              # all six, 2 at a time
    python -m scripts.validate_examples --only discharge-report --show 20
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.config import settings
from app.fhir import store
from app.fhir.capability import PROFILE_EU_BUNDLE, PROFILE_EU_MPD
from app.fhir.document import compile_document
from app.fhir.examples import reference_patient
from app.fhir.validation_queue import (
    OFFLINE_NOTE,
    _is_offline_artefact,
    _issue_text,
    validator_command,
    validator_lock,
)


def examples() -> dict[str, tuple[dict, str]]:
    p = reference_patient()
    if p is None:
        raise SystemExit("no reference patient in the store; run python -m scripts.seed")
    out: dict[str, tuple[dict, str]] = {}
    for cat, profile in PROFILE_EU_BUNDLE.items():
        out[cat] = (compile_document(p["id"], cat), profile)
    for label, rtype in (("eprescription", "MedicationRequest"), ("edispensation", "MedicationDispense")):
        res = next((r for r in store.search(rtype, {"patient": [p["id"]]})), None)
        if res is None:
            raise SystemExit(f"no {rtype} for the reference patient")
        out[label] = (res, PROFILE_EU_MPD[rtype])
    return out


def _validate(res: dict, profile: str) -> dict:
    """Run the validator; keep each error's full text (with location)."""
    with tempfile.TemporaryDirectory() as td:
        src, out = Path(td) / "input.json", Path(td) / "out.json"
        src.write_text(json.dumps(res))
        with validator_lock():
            subprocess.run(validator_command(src, out, profile), capture_output=True, text=True,
                           timeout=settings.validation_timeout_seconds)
        if not out.exists():
            raise RuntimeError("validator produced no report")
        issues = json.loads(out.read_text()).get("issue", [])
    offline = (settings.validator_tx or "n/a").lower() == "n/a"
    errors = [i for i in issues if i.get("severity") in ("error", "fatal")
              and not (offline and _is_offline_artefact(i))]
    return {"ok": not errors, "errors": len(errors),
            "warnings": sum(1 for i in issues if i.get("severity") == "warning"),
            "issues": [{"severity": "error", "text": _issue_text(i)[:400], "count": 1} for i in errors],
            "note": OFFLINE_NOTE if offline else ""}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", help="example key (repeatable)")
    ap.add_argument("--show", type=int, default=8, help="distinct issues to print per example")
    ap.add_argument("--parallel", type=int, default=2)
    args = ap.parse_args()
    todo = {k: v for k, v in examples().items() if not args.only or k in args.only}

    def run(item):
        key, (res, profile) = item
        try:
            return key, _validate(res, profile)
        except Exception as e:  # noqa: BLE001
            return key, {"ok": False, "errors": -1, "warnings": 0, "issues": [{"severity": "error", "text": str(e), "count": 1}]}

    failed = False
    with ThreadPoolExecutor(max_workers=args.parallel) as ex:
        for key, r in ex.map(run, todo.items()):
            failed |= not r["ok"]
            print(f"{'PASS' if r['ok'] else 'FAIL'} {key}: {r['errors']} errors, {r['warnings']} warnings", flush=True)
            for i in [i for i in r["issues"] if i["severity"] == "error"][: args.show]:
                print(f"    x{i['count']} {i['text']}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
