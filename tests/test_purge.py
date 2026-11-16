"""scripts/purge.py deletes live data, so pin down exactly what it touches."""
from __future__ import annotations

import base64
from pathlib import Path

import pytest

from app.config import settings
from app.fhir import store
from app.fhir.origin import REFERENCE, origin_of

pytestmark = pytest.mark.asyncio


async def _publish_note(client, auth_headers, pid, text: bytes) -> str:
    doc = {"resourceType": "DocumentReference", "status": "current", "subject": {"reference": f"Patient/{pid}"},
           "content": [{"attachment": {"contentType": "text/plain", "data": base64.b64encode(text).decode()}}]}
    r = await client.post("/DocumentReference", headers=auth_headers, json=doc)
    assert r.status_code == 201, r.text[:300]
    return r.json()["id"]


def _reference_files() -> set[Path]:
    out = set()
    for rtype in store.SUPPORTED_TYPES:
        for res in store.list_all(rtype):
            if origin_of(res)["kind"] == REFERENCE:
                out.add(store.dir_for_type(rtype) / f"{res['id']}.json")
    return out


async def test_purge_removes_exactly_the_named_submissions(client, auth_headers, pid, tmp_path, monkeypatch):
    from scripts import purge
    a = await _publish_note(client, auth_headers, pid, b"note a")
    b = await _publish_note(client, auth_headers, pid, b"note b")
    keep = await _publish_note(client, auth_headers, pid, b"note kept")
    reference_before = _reference_files()
    monkeypatch.setattr(purge, "BACKUP_DIR", tmp_path)

    assert purge.main(["--submission", a, "--submission", b]) == 0  # dry run
    assert store.read("DocumentReference", a) is not None

    assert purge.main(["--submission", a, "--submission", b, "--apply"]) == 0
    store.invalidate_cache()
    for gone in (a, b):
        assert store.read("DocumentReference", gone) is None
        assert store.read("Binary", gone) is None
    assert store.read("DocumentReference", keep) is not None
    assert store.read("Binary", keep) is not None
    assert _reference_files() == reference_before
    assert len(list(tmp_path.glob("data-*.tar.gz"))) == 1, "one backup per run, not per submission"


async def test_purge_with_nothing_to_do_writes_no_backup(tmp_path, monkeypatch):
    from scripts import purge
    monkeypatch.setattr(purge, "BACKUP_DIR", tmp_path)
    assert purge.main(["--submission", "no-such-submission", "--apply"]) == 0
    assert list(tmp_path.iterdir()) == []
    assert Path(settings.data_dir).exists()
