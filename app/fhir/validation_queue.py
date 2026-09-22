"""Asynchronous EU-profile validation — a badge, never a gate.

Every ITI-105 submission is accepted first (201) and *then* run through the
HL7 java validator against the EU IG package for its priority category. The
result is persisted as ``data/validation/<key>.json`` so the UI's Coverage
page can show one of four states per bundle:

* ``pending``      — queued or running
* ``validated``    — zero errors against the profile (warnings allowed)
* ``failed``       — at least one error, with the distinct errors collapsed
* ``unavailable``  — the validator could not give an answer (jar or packages
                     missing, JVM crash, terminology-server timeout). This is
                     deliberately separate from ``failed``: a flaky validator
                     must never read as "your bundle is wrong". ``retry``
                     re-queues it.

Reference documents (the seeded panel's compiled bundles) go through the same
path via :func:`enqueue_reference`, so the baseline the community is measured
against carries the same badge.

Implementation notes
--------------------
* One daemon worker thread per process, jobs run strictly one at a time (the
  validator is CPU-heavy and the box is small). Under gunicorn each worker
  process has its own queue; records live on disk so any process answers
  ``status`` consistently.
* The java process is started with ``-Duser.home=<validator_home>`` so its
  package cache (``.fhir/packages``) persists under the data dir instead of an
  ephemeral PrivateTmp $HOME. ``-tx n/a`` keeps it offline: terminology
  checks against tx.fhir.org are the single biggest source of flakiness and
  are not what the badge is about.
* ``_run_validator`` and ``_validator_available`` are module attributes so
  tests inject fakes without a JVM.
"""
from __future__ import annotations

import json
import logging
import queue
import re
import subprocess
import tempfile
import threading
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.fhir.capability import PROFILE_EU_BUNDLE

log = logging.getLogger("ehds.validation")

PENDING, VALIDATED, FAILED, UNAVAILABLE = "pending", "validated", "failed", "unavailable"
STATES = (PENDING, VALIDATED, FAILED, UNAVAILABLE)

# a record that has sat in `pending` longer than this is presumed orphaned by a
# process restart and is re-queued on the next startup
_STALE_PENDING_SECONDS = 15 * 60


# ---------- records ----------

def _dir() -> Path:
    return settings.data_dir / "validation"


def record_path(key: str) -> Path:
    if "/" in key or ".." in key:
        raise ValueError("bad validation key")
    return _dir() / f"{key}.json"


def status(key: str) -> dict[str, Any] | None:
    try:
        return json.loads(record_path(key).read_text())
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return None


def _write(key: str, rec: dict[str, Any]) -> dict[str, Any]:
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    rec["key"] = key
    rec["updated"] = _now()
    tmp = record_path(key).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, indent=2, sort_keys=True))
    tmp.replace(record_path(key))
    return rec


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def reference_key(patient_id: str, category: str) -> str:
    return f"reference-{patient_id}-{category}"


def all_records() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    d = _dir()
    if not d.exists():
        return out
    for fp in d.glob("*.json"):
        try:
            rec = json.loads(fp.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        out[fp.stem] = rec
    return out


# ---------- availability ----------

def _validator_available() -> tuple[bool, str]:
    """(ok, reason). Swapped by tests."""
    jar = settings.validator_jar
    if not jar.exists():
        return False, f"validator jar not installed at {jar}"
    pkgs = settings.eu_packages_dir
    if not pkgs.exists() or not any(pkgs.glob("*.tgz")):
        return False, f"no EU IG packages (.tgz) under {pkgs}"
    return True, ""


# ---------- the validator itself ----------

_INDEX_RE = re.compile(r"\[\d+\]")
_LONG_TOKEN_RE = re.compile(r"[A-Za-z0-9._:/-]{24,}")


def _issue_text(issue: dict[str, Any]) -> str:
    loc = ".".join(issue.get("expression") or []) or (issue.get("location") or [""])[0]
    txt = (issue.get("details") or {}).get("text") or issue.get("diagnostics") or "(no text)"
    return f"{loc}: {txt}" if loc else txt


def _normalise(text: str) -> str:
    """fold entry indices and long ids so 200 copies of one root cause count as one."""
    return _LONG_TOKEN_RE.sub("<id>", _INDEX_RE.sub("[N]", text))


def collapse_issues(issues: list[dict[str, Any]], *, top: int = 25) -> dict[str, Any]:
    """Summarise a validator OperationOutcome issue list: counts plus the
    distinct error/warning messages with how often each occurred."""
    errors = [i for i in issues if i.get("severity") in ("error", "fatal")]
    warnings = [i for i in issues if i.get("severity") == "warning"]
    out: list[dict[str, Any]] = []
    for sev, group in (("error", errors), ("warning", warnings)):
        counted = Counter(_normalise(_issue_text(i)) for i in group)
        for text, n in counted.most_common(top):
            out.append({"severity": sev, "text": text[:300], "count": n})
    return {"errors": len(errors), "warnings": len(warnings), "issues": out}


def _run_validator(bundle: dict[str, Any], category: str | None, profile: str | None) -> dict[str, Any]:
    """Run the HL7 java validator against ``bundle``. Returns
    ``{"ok", "errors", "warnings", "issues"}``; raises on anything that is
    not a verdict (timeout, crash, unparseable output). Swapped by tests."""
    home = settings.validator_home
    home.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "input.json"
        src.write_text(json.dumps(bundle))
        out = Path(td) / "out.json"
        cmd = ["java", f"-Duser.home={home}", "-jar", str(settings.validator_jar), str(src),
               "-version", "4.0.1", "-tx", "n/a", "-output", str(out)]
        for tgz in sorted(settings.eu_packages_dir.glob("*.tgz")):
            cmd += ["-ig", str(tgz)]
        if profile:
            cmd += ["-profile", profile]
        started = time.monotonic()
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=settings.validation_timeout_seconds)
        log.info("validator finished key=%s rc=%s in %.0fs", bundle.get("id"), proc.returncode,
                 time.monotonic() - started)
        if not out.exists():
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
            raise RuntimeError("validator produced no report: " + " | ".join(tail))
        report = json.loads(out.read_text())
    summary = collapse_issues(report.get("issue", []))
    summary["ok"] = summary["errors"] == 0
    return summary


# ---------- job loading ----------

def _load_submission(inbox_id: str) -> dict[str, Any]:
    fp = settings.data_dir / "inbox" / f"{inbox_id}.json"
    return json.loads(fp.read_text())


def _load_job_bundle(rec: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """(bundle, category) for a record of either kind."""
    from app.fhir.origin import category_of_bundle
    if rec.get("kind") == "reference":
        from app.fhir.document import compile_document
        bundle = compile_document(rec["patient"], rec["category"])
        return bundle, rec["category"]
    bundle = _load_submission(rec["submission"])
    return bundle, category_of_bundle(bundle)


# ---------- worker ----------

_queue: queue.Queue[str] = queue.Queue()
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()


def _on_job_done(key: str) -> None:  # test hook
    return None


def _process(key: str) -> None:
    rec = status(key)
    if rec is None:
        return
    rec["started"] = _now()
    _write(key, rec)
    try:
        bundle, category = _load_job_bundle(rec)
        profile = PROFILE_EU_BUNDLE.get(category or "")
        result = _run_validator(bundle, category, profile)
        rec.update({
            "state": VALIDATED if result["ok"] else FAILED,
            "category": category,
            "profile": profile,
            "errors": result.get("errors", 0),
            "warnings": result.get("warnings", 0),
            "issues": result.get("issues", []),
            "reason": None,
            "finished": _now(),
        })
    except Exception as e:  # noqa: BLE001 — every non-verdict becomes `unavailable`
        log.warning("validation unavailable key=%s: %s", key, e)
        rec.update({"state": UNAVAILABLE, "reason": str(e)[:500], "finished": _now()})
    _write(key, rec)
    _on_job_done(key)


def _worker_loop() -> None:
    while True:
        key = _queue.get()
        try:
            _process(key)
        except Exception:  # noqa: BLE001
            log.exception("validation worker crashed on %s", key)
        finally:
            _queue.task_done()


def _ensure_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_worker_loop, name="eu-validation", daemon=True)
            _worker.start()


def _submit(key: str, rec: dict[str, Any]) -> dict[str, Any]:
    ok, reason = _validator_available()
    rec["attempts"] = int(rec.get("attempts") or 0) + 1
    if not ok:
        rec.update({"state": UNAVAILABLE, "reason": reason, "finished": _now()})
        return _write(key, rec)
    rec.update({"state": PENDING, "reason": None, "queued": _now(), "started": None, "finished": None})
    _write(key, rec)
    _ensure_worker()
    _queue.put(key)
    return rec


# ---------- public API ----------

def enqueue_submission(inbox_id: str) -> dict[str, Any]:
    """Queue EU-profile validation of an inbox bundle. Returns the record."""
    existing = status(inbox_id) or {}
    rec = {"kind": "submission", "submission": inbox_id, "attempts": existing.get("attempts", 0)}
    return _submit(inbox_id, rec)


def enqueue_reference(patient_id: str, category: str) -> dict[str, Any]:
    """Queue validation of a compiled reference document. Returns the record."""
    key = reference_key(patient_id, category)
    existing = status(key) or {}
    rec = {"kind": "reference", "patient": patient_id, "category": category,
           "attempts": existing.get("attempts", 0)}
    return _submit(key, rec)


def retry(key: str) -> dict[str, Any] | None:
    """Re-queue a finished record (any state). None if the key is unknown."""
    rec = status(key)
    if rec is None:
        return None
    if rec.get("kind") == "reference":
        return enqueue_reference(rec["patient"], rec["category"])
    return enqueue_submission(rec["submission"])


def resume_pending() -> int:
    """Re-queue records left `pending` by a previous process. Called at startup.
    Only records older than the stale window are picked up so two gunicorn
    workers starting together do not both run the same job."""
    n = 0
    now = time.time()
    for key, rec in all_records().items():
        if rec.get("state") != PENDING:
            continue
        ts = rec.get("started") or rec.get("queued") or rec.get("updated")
        try:
            age = now - datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() if ts else 1e9
        except ValueError:
            age = 1e9
        if age >= _STALE_PENDING_SECONDS and retry(key) is not None:
            n += 1
    return n


def _reset_for_tests() -> None:
    """Drop the record dir and forget the worker (tests only)."""
    import shutil
    shutil.rmtree(_dir(), ignore_errors=True)
    while not _queue.empty():
        try:
            _queue.get_nowait()
            _queue.task_done()
        except queue.Empty:
            break
