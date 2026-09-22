"""Activity page: audit-log classification, filters, and the scenario run id."""
from __future__ import annotations

import json
import uuid
from datetime import date

import pytest

from app import audit_log
from app.config import settings


@pytest.mark.parametrize("path,expected", [
    ("/", "fhir"), ("/Patient", "fhir"), ("/Patient/abc", "fhir"), ("/Patient/abc/$everything", "fhir"),
    ("/DocumentReference", "fhir"), ("/Bundle/x", "fhir"), ("/token", "fhir"), ("/metadata", "fhir"),
    ("/.well-known/smart-configuration", "fhir"), ("/register-client", "fhir"), ("/register-client/x", "fhir"),
    ("/spec/all-bundle-ids", "fhir"), ("/Epic/$import", "fhir"), ("/healthz", "fhir"), ("/llms.txt", "fhir"),
    ("/ui/", "ui"), ("/ui/api/audit", "ui"), ("/ui", "ui"),
    ("/wp-config.php", "noise"), ("/.env", "noise"), ("/Patients", "noise"), ("/admin", "noise"),
    ("/PatientX/1", "noise"),
])
def test_classify(path, expected):
    assert audit_log.classify(path) == expected


@pytest.mark.parametrize("path,expected", [
    ("/Patient/3f2a9c1e-5b7d-4e8a-9c21-7d4e5f6a8b90", "/Patient/{id}"),
    ("/Patient/3f2a9c1e-5b7d-4e8a-9c21-7d4e5f6a8b90/$everything", "/Patient/{id}/$everything"),
    ("/Patient/$match", "/Patient/$match"),
    ("/Patient", "/Patient"),
    ("/Bundle/abc", "/Bundle/{id}"),
    ("/register-client/my-app", "/register-client/my-app"),
    ("/ui/api/submissions/3f2a9c1e-5b7d-4e8a-9c21-7d4e5f6a8b90", "/ui/api/submissions/{id}"),
])
def test_collapse_path(path, expected):
    assert audit_log.collapse_path(path) == expected


def _append(entries: list[dict]) -> None:
    d = settings.audit_log_dir
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"audit-{date.today().isoformat()}.jsonl", "a", encoding="utf-8") as f:
        for e in entries:
            base = {"ts": "2026-09-22T10:00:00.000Z", "method": "GET", "status": 200, "dur_ms": 5,
                    "client_id": None, "ip": "127.0.0.1", "ua": "t", "req_bytes": 0, "resp_bytes": 10,
                    "query": None, "run": None}
            f.write(json.dumps({**base, **e}) + "\n")


def test_query_defaults_to_fhir_scope_and_filters(tmp_path):
    marker = uuid.uuid4().hex
    _append([
        {"path": "/wp-config.php", "status": 404, "ua": marker},
        {"path": f"/Patient/{marker}", "client_id": "alice", "run": "run-1"},
        {"path": "/Observation", "query": f"patient={marker}", "client_id": "bob", "run": "run-1"},
        {"path": "/ui/api/audit", "ua": marker},
        {"path": "/token", "method": "POST", "client_id": "alice", "run": "run-2"},
    ])
    fhir = audit_log.query(limit=1000)
    paths = [e["path"] for e in fhir["entries"]]
    assert "/wp-config.php" not in paths and "/ui/api/audit" not in paths
    assert f"/Patient/{marker}" in paths
    assert all(e["scope"] == "fhir" for e in fhir["entries"])

    everything = audit_log.query(limit=1000, scope="all")
    assert any(e["path"] == "/wp-config.php" for e in everything["entries"])

    by_patient = audit_log.query(limit=1000, patient=marker)
    assert sorted(e["path"] for e in by_patient["entries"]) == ["/Observation", f"/Patient/{marker}"]

    by_run = audit_log.query(limit=1000, run="run-1")
    assert len(by_run["entries"]) == 2
    by_client = audit_log.query(limit=1000, client_id="alice", run="run-2")
    assert [e["path"] for e in by_client["entries"]] == ["/token"]
    assert audit_log.query(limit=1000, method="post", run="run-2")["entries"][0]["path"] == "/token"


def test_stats_scope_and_grouping():
    marker = uuid.uuid4().hex
    _append([
        {"path": f"/Patient/{marker}", "client_id": "carol"},
        {"path": f"/Patient/{marker}", "client_id": "carol"},
        {"path": "/.env", "status": 404},
    ])
    s = audit_log.stats(days=1)
    assert s["scope"] == "fhir"
    assert s["by_scope"]["noise"] >= 1
    assert dict(s["top_paths"]).get("/Patient/{id}", 0) >= 2
    assert dict(s["top_clients"]).get("carol", 0) >= 2
    assert s["latency_ms"]["p50"] is not None


@pytest.mark.asyncio
async def test_middleware_records_run_header_and_ui_endpoint_reads_it(client, auth_headers, pid):
    run = f"t-{uuid.uuid4().hex[:8]}"
    r = await client.get(f"/Patient/{pid}", headers={**auth_headers, "X-Demo-Run": run})
    assert r.status_code == 200
    r = await client.get("/ui/api/audit", params={"run": run})
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["path"] == f"/Patient/{pid}"
    assert entries[0]["client_id"] == "test-client"
    assert entries[0]["run"] == run
    r = await client.get("/ui/api/audit/stats", params={"scope": "all"})
    assert r.status_code == 200 and r.json()["total"] >= 1
    assert (await client.get("/ui/api/audit", params={"scope": "bogus"})).status_code == 422
