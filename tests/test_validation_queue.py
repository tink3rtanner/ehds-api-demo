"""Asynchronous EU-profile validation (app/fhir/validation_queue.py).

Validation is a badge, never a gate: a submission is accepted with 201 first,
then queued. Each job ends in exactly one of four states — pending, validated,
failed, unavailable — persisted as data/validation/<key>.json so every
gunicorn worker (and the UI) reads the same answer.
"""
from __future__ import annotations

import json
import threading

import pytest

from app.config import settings
from app.fhir import validation_queue as vq


@pytest.fixture(autouse=True)
def _isolated_queue(monkeypatch):
    """Each test gets a fresh worker, no leftover records, and the inbox
    bundles it wrote are removed again (the coverage tests count the inbox)."""
    inbox = settings.data_dir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in inbox.glob("*.json")}
    vq._reset_for_tests()
    yield
    vq._reset_for_tests()
    for p in inbox.glob("*.json"):
        if p.name not in before:
            p.unlink()


def _write_inbox(bundle_id: str, category_code: str = "60591-5") -> dict:
    bundle = {"resourceType": "Bundle", "type": "document", "id": bundle_id, "entry": [
        {"resource": {"resourceType": "Composition", "id": "c",
                      "type": {"coding": [{"system": "http://loinc.org", "code": category_code}]}}},
        {"resource": {"resourceType": "Patient", "id": "p", "address": [{"country": "AT"}]}},
    ]}
    inbox = settings.data_dir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / f"{bundle_id}.json").write_text(json.dumps(bundle))
    return bundle


def test_unavailable_when_validator_is_not_installed():
    """conftest points EHDS_VALIDATOR_JAR at a missing file: no thread, no java."""
    _write_inbox("sub-unavail")
    rec = vq.enqueue_submission("sub-unavail")
    assert rec["state"] == "unavailable"
    assert "jar" in rec["reason"].lower() or "package" in rec["reason"].lower()
    assert vq.status("sub-unavail")["state"] == "unavailable"
    assert (settings.data_dir / "validation" / "sub-unavail.json").exists()


def test_validated_and_failed_states_via_injected_runner(monkeypatch):
    done = threading.Event()
    calls: list[tuple[str | None, str | None]] = []

    def fake_runner(bundle, category, profile):
        calls.append((category, profile))
        if bundle["id"] == "sub-good":
            return {"ok": True, "errors": 0, "warnings": 2, "issues": []}
        return {"ok": False, "errors": 3, "warnings": 0,
                "issues": [{"severity": "error", "text": "Bundle.entry[1]: bad", "count": 3}]}

    monkeypatch.setattr(vq, "_validator_available", lambda: (True, ""))
    monkeypatch.setattr(vq, "_run_validator", fake_runner)
    monkeypatch.setattr(vq, "_on_job_done", lambda key: done.set() if key == "sub-bad" else None)

    _write_inbox("sub-good", "60591-5")
    _write_inbox("sub-bad", "11502-2")
    first = vq.enqueue_submission("sub-good")
    assert first["state"] == "pending"
    vq.enqueue_submission("sub-bad")
    assert done.wait(5), "worker did not finish"

    good = vq.status("sub-good")
    assert good["state"] == "validated"
    assert good["warnings"] == 2
    assert good["profile"] == "http://hl7.eu/fhir/eps/StructureDefinition/bundle-eu-eps"
    bad = vq.status("sub-bad")
    assert bad["state"] == "failed"
    assert bad["errors"] == 3
    assert bad["issues"][0]["text"].startswith("Bundle.entry")
    assert bad["profile"] == "http://hl7.eu/fhir/laboratory/StructureDefinition/Bundle-eu-lab"
    assert ("patient-summary", good["profile"]) in calls
    assert ("laboratory-report", bad["profile"]) in calls


def test_prescription_validates_against_base_r4_only(monkeypatch):
    done = threading.Event()
    seen = {}

    def fake_runner(bundle, category, profile):
        seen["profile"] = profile
        return {"ok": True, "errors": 0, "warnings": 0, "issues": []}

    monkeypatch.setattr(vq, "_validator_available", lambda: (True, ""))
    monkeypatch.setattr(vq, "_run_validator", fake_runner)
    monkeypatch.setattr(vq, "_on_job_done", lambda key: done.set())
    _write_inbox("sub-rx", "57833-6")
    vq.enqueue_submission("sub-rx")
    assert done.wait(5)
    assert seen["profile"] is None
    assert vq.status("sub-rx")["state"] == "validated"


def test_runner_exception_becomes_unavailable_not_failed(monkeypatch):
    """A flaky validator (tx timeouts, JVM crash) must not read as 'your bundle is wrong'."""
    done = threading.Event()

    def boom(bundle, category, profile):
        raise RuntimeError("terminology server timeout")

    monkeypatch.setattr(vq, "_validator_available", lambda: (True, ""))
    monkeypatch.setattr(vq, "_run_validator", boom)
    monkeypatch.setattr(vq, "_on_job_done", lambda key: done.set())
    _write_inbox("sub-flaky")
    vq.enqueue_submission("sub-flaky")
    assert done.wait(5)
    rec = vq.status("sub-flaky")
    assert rec["state"] == "unavailable"
    assert "timeout" in rec["reason"]


def test_retry_requeues_a_finished_record(monkeypatch):
    done = threading.Event()
    attempts = {"n": 0}

    def flaky_then_ok(bundle, category, profile):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("first attempt fails")
        return {"ok": True, "errors": 0, "warnings": 0, "issues": []}

    monkeypatch.setattr(vq, "_validator_available", lambda: (True, ""))
    monkeypatch.setattr(vq, "_run_validator", flaky_then_ok)
    monkeypatch.setattr(vq, "_on_job_done", lambda key: done.set())
    _write_inbox("sub-retry")
    vq.enqueue_submission("sub-retry")
    assert done.wait(5)
    assert vq.status("sub-retry")["state"] == "unavailable"
    done.clear()
    rec = vq.retry("sub-retry")
    assert rec["state"] == "pending"
    assert done.wait(5)
    assert vq.status("sub-retry")["state"] == "validated"
    assert vq.status("sub-retry")["attempts"] == 2


def test_status_of_unknown_key_is_none():
    assert vq.status("nope") is None
    assert vq.retry("nope") is None


def test_reference_documents_can_be_validated_too(monkeypatch, pid):
    """Open decision 6: reference docs get the same badge as community ones."""
    done = threading.Event()
    seen = {}

    def fake_runner(bundle, category, profile):
        seen["type"] = bundle.get("type")
        seen["entries"] = len(bundle.get("entry", []))
        return {"ok": True, "errors": 0, "warnings": 1, "issues": []}

    monkeypatch.setattr(vq, "_validator_available", lambda: (True, ""))
    monkeypatch.setattr(vq, "_run_validator", fake_runner)
    monkeypatch.setattr(vq, "_on_job_done", lambda key: done.set())
    rec = vq.enqueue_reference(pid, "patient-summary")
    assert rec["state"] == "pending"
    assert rec["kind"] == "reference"
    assert done.wait(5)
    assert seen["type"] == "document" and seen["entries"] > 1
    assert vq.status(vq.reference_key(pid, "patient-summary"))["state"] == "validated"


def test_collapse_issues_folds_repeated_errors():
    issues = [
        {"severity": "error", "expression": [f"Bundle.entry[{i}].resource"], "details": {"text": "Observation.code: none of the codings are in the expected value set"}}
        for i in range(40)
    ] + [{"severity": "warning", "diagnostics": "Wrong Display Name 'X' for http://loinc.org#1"}]
    out = vq.collapse_issues(issues)
    assert out["errors"] == 40
    assert out["warnings"] == 1
    assert len(out["issues"]) == 2
    top = out["issues"][0]
    assert top["severity"] == "error" and top["count"] == 40
    assert "[N]" in top["text"]


def test_validator_command_pins_tx_setting_and_every_eu_package(tmp_path, monkeypatch):
    """The -tx flag follows settings (offline by default) and every .tgz in the
    package dir is loaded."""

    pkgs = tmp_path / "pkgs"
    pkgs.mkdir()
    for name in ("eps", "base"):
        (pkgs / f"{name}.tgz").write_bytes(b"")
    import dataclasses
    monkeypatch.setattr(vq, "settings", dataclasses.replace(settings, eu_packages_dir=pkgs))
    cmd = vq.validator_command(tmp_path / "in.json", tmp_path / "out.json", "http://hl7.eu/fhir/eps/StructureDefinition/bundle-eu-eps")
    assert cmd[cmd.index("-tx") + 1] == settings.validator_tx == "n/a"
    assert cmd[cmd.index("-version") + 1] == "4.0.1"
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "-ig"] == [str(pkgs / "base.tgz"), str(pkgs / "eps.tgz")]
    assert cmd[cmd.index("-profile") + 1].endswith("bundle-eu-eps")
    assert any(a.startswith("-Duser.home=") for a in cmd)


def test_offline_downgrades_only_the_slice_ambiguity_artefact():
    """Offline, 'matches more than one slice' on Bundle.entry becomes a labelled
    warning; a genuine error in the same report still fails the bundle."""
    slice_err = {"severity": "error", "expression": ["Bundle.entry[3]"],
                 "details": {"text": "Profile http://hl7.eu/fhir/eps/StructureDefinition/bundle-eu-eps|1.0.0-ci-build, "
                                     "Element matches more than one slice - observation-pregnancy-edd, observation-vital-signs"}}
    real_err = {"severity": "error", "expression": ["Bundle.entry[0].resource"],
                "details": {"text": "Composition.subject: minimum required = 1, but only found 0"}}
    offline = vq.collapse_issues([slice_err, slice_err, real_err], offline=True)
    assert offline["errors"] == 1 and offline["downgraded"] == 2 and offline["warnings"] == 2
    assert any(i["text"].startswith("[offline slice ambiguity]") and i["count"] == 2 for i in offline["issues"])
    assert any(i["severity"] == "error" and "Composition.subject" in i["text"] for i in offline["issues"])
    # clean apart from the artefact -> passes offline
    clean = vq.collapse_issues([slice_err], offline=True)
    assert clean["errors"] == 0 and clean["downgraded"] == 1
    # with a terminology server nothing is downgraded
    online = vq.collapse_issues([slice_err], offline=False)
    assert online["errors"] == 1 and online["downgraded"] == 0
