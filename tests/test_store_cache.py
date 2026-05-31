"""store index-cache coherence.

Regression guard for the multi-worker discoverability bug: under gunicorn the
service runs several pre-forked workers, each with its own in-process
``store._cache``. A write handled by one worker invalidates only that worker's
cache, so a sibling worker kept serving a stale index until restart — submitted
(ITI-105) data appeared/disappeared depending on which worker answered.

The fix tags each cache entry with a cheap signature of the on-disk type-dir
and re-stats on every read. These tests simulate "another worker wrote to disk"
by mutating files DIRECTLY, bypassing ``store.write``/``invalidate_cache`` — so
the only way the change can be seen is the signature check. The API-level test
asserts the end-to-end property: freshly submitted data is discoverable.
"""
from __future__ import annotations

import json
import os

import pytest

from app.fhir import store


def _patient(pid: str, family: str) -> dict:
    return {
        "resourceType": "Patient",
        "id": pid,
        "name": [{"family": family, "given": ["Cache"]}],
        "gender": "other",
    }


def _write_raw(rtype: str, resource: dict) -> os.PathLike:
    """write a resource file straight to disk, bypassing store.write().

    This mimics a DIFFERENT gunicorn worker (or any out-of-band writer)
    touching the data dir: the current process's cache is NOT invalidated.
    """
    d = store.dir_for_type(rtype)
    d.mkdir(parents=True, exist_ok=True)
    fp = d / f"{resource['id']}.json"
    fp.write_text(json.dumps(resource, indent=2, sort_keys=True))
    return fp


def test_external_add_is_picked_up():
    pid = "zz-cache-add-probe"
    fp = store.dir_for_type("Patient") / f"{pid}.json"
    try:
        store.list_all("Patient")              # warm the cache for Patient
        assert store.read("Patient", pid) is None

        _write_raw("Patient", _patient(pid, "AddedExternally"))  # "other worker"

        got = store.read("Patient", pid)        # signature changed -> reload
        assert got is not None
        assert got["name"][0]["family"] == "AddedExternally"
        assert pid in {r["id"] for r in store.list_all("Patient")}
    finally:
        fp.unlink(missing_ok=True)
        store.invalidate_cache("Patient")


def test_external_in_place_update_is_picked_up():
    pid = "zz-cache-update-probe"
    fp = _write_raw("Patient", _patient(pid, "Before"))
    try:
        # warm cache with the original content
        assert store.read("Patient", pid)["name"][0]["family"] == "Before"

        # rewrite the SAME file (count unchanged) and force a strictly-newer
        # mtime so the test is hermetic regardless of fs timestamp resolution.
        prev_ns = fp.stat().st_mtime_ns
        fp.write_text(json.dumps(_patient(pid, "After"), indent=2, sort_keys=True))
        os.utime(fp, ns=(prev_ns + 1_000_000_000, prev_ns + 1_000_000_000))

        assert store.read("Patient", pid)["name"][0]["family"] == "After"
    finally:
        fp.unlink(missing_ok=True)
        store.invalidate_cache("Patient")


def test_external_delete_is_picked_up():
    pid = "zz-cache-delete-probe"
    fp = _write_raw("Patient", _patient(pid, "Doomed"))
    try:
        assert store.read("Patient", pid) is not None   # warm + present
        fp.unlink()                                       # "other worker" deletes
        assert store.read("Patient", pid) is None         # count changed -> reload
        assert pid not in {r["id"] for r in store.list_all("Patient")}
    finally:
        fp.unlink(missing_ok=True)
        store.invalidate_cache("Patient")


@pytest.mark.asyncio
async def test_submitted_patient_is_discoverable(client, auth_headers):
    """ITI-105 submit -> the new Patient is readable AND shows up in search."""
    pid = "zz-submitted-discoverable"
    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {
                "resource": _patient(pid, "Submitted"),
                "request": {"method": "PUT", "url": f"Patient/{pid}"},
            },
        ],
    }
    try:
        r = await client.post(
            "/",
            headers={**auth_headers, "Content-Type": "application/fhir+json"},
            json=bundle,
        )
        assert r.status_code == 201, r.text
        # submit naturalizes to a local id; follow the returned location
        loc = r.json()["entry"][0]["response"]["location"]
        local_id = loc.split("/", 1)[1]

        read = await client.get(f"/{loc}", headers=auth_headers)
        assert read.status_code == 200, read.text

        search = await client.get("/Patient?_count=500", headers=auth_headers)
        assert search.status_code == 200
        ids = {e["resource"]["id"] for e in search.json().get("entry", [])}
        assert local_id in ids
    finally:
        local_id = locals().get("local_id", pid)
        (store.dir_for_type("Patient") / f"{local_id}.json").unlink(missing_ok=True)
        store.invalidate_cache("Patient")
