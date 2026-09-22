"""The UI's non-FHIR helper endpoints and the static app itself."""
from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.asyncio


async def test_viewer_token_is_read_only_and_works_against_the_front_door(client, pid):
    r = await client.post("/ui/api/viewer-token")
    assert r.status_code == 200
    tok = r.json()
    assert tok["scope"] == "system/*.read"
    assert ".write" not in tok["scope"]
    assert tok["client_id"] == "ui-viewer"
    h = {"Authorization": f"Bearer {tok['access_token']}"}
    assert (await client.get(f"/Patient/{pid}", headers=h)).status_code == 200
    assert (await client.get("/DocumentReference", headers=h)).status_code == 200
    # ...but cannot publish
    w = await client.post("/", headers={**h, "Content-Type": "application/fhir+json"},
                          json={"resourceType": "Bundle", "type": "transaction", "entry": []})
    assert w.status_code == 403


async def test_no_fhir_content_is_served_by_the_ui_api(client, pid):
    """Design goal 2: the old privileged read paths are gone."""
    for path in ("/ui/api/patients", f"/ui/api/patients/{pid}", f"/ui/api/raw/Patient/{pid}",
                 "/ui/api/proxy?path=/Patient", "/ui/api/documents", "/ui/api/dev-token",
                 f"/ui/api/patients/{pid}/doc/patient-summary"):
        r = await client.get(path)
        assert r.status_code in (404, 405), path


async def test_examples_endpoint_has_no_stale_ids(client):
    r = await client.get("/ui/api/examples")
    assert r.status_code == 200
    ex = r.json()
    assert ex["patient"]["slot"] == "p-001"
    assert re.fullmatch(r"[0-9a-f-]{36}", ex["patient"]["id"])
    for info in ex["documents"].values():
        assert info["path"] == f"/Bundle/{info['bundle_id']}"
    assert not re.search(r"/(Patient|Bundle)/p-\d{3}", str(ex))


async def test_server_info_counts_origin(client):
    r = await client.get("/ui/api/server-info")
    assert r.status_code == 200
    info = r.json()
    assert info["patients_by_origin"]["reference"] == 10
    assert info["categories"] == ["patient-summary", "laboratory-report", "discharge-report",
                                  "imaging-report", "prescription"]
    assert info["token_endpoint"].endswith("/token")


async def test_build_info_reports_validator_availability(client):
    r = await client.get("/ui/api/build-info")
    assert r.status_code == 200
    v = r.json()["validator"]
    assert v["available"] is False  # test env points at a missing jar
    assert v["reason"]


async def test_clients_endpoint_shows_ids_and_time_only(client):
    reg = await client.post("/register-client", json={
        "client_id": "ui-api-test-client", "scopes": ["system/*.read"],
        "jwk": {"kty": "RSA", "n": "AQAB", "e": "AQAB"}})
    assert reg.status_code in (200, 201), reg.text
    r = await client.get("/ui/api/clients")
    assert r.status_code == 200
    row = next(c for c in r.json()["clients"] if c["client_id"] == "ui-api-test-client")
    assert set(row) == {"client_id", "registered_at", "scope_count", "can_write"}
    assert row["registered_at"] and row["can_write"] is False
    await client.delete("/register-client/ui-api-test-client")


async def test_qr_only_encodes_our_urls(client):
    ok = await client.get("/ui/api/qr", params={"text": "http://testserver/ui/#/patients"})
    assert ok.status_code == 200 and ok.headers["content-type"].startswith("image/svg+xml")
    rel = await client.get("/ui/api/qr", params={"text": "/ui/#/coverage"})
    assert rel.status_code == 200
    bad = await client.get("/ui/api/qr", params={"text": "https://evil.example/phish"})
    assert bad.status_code == 400


async def test_static_app_is_served(client):
    r = await client.get("/ui/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    html = r.text
    assert '<script type="module"' in html
    assert "EU Health Data API" in html
    for asset in ("/ui/app.css", "/ui/app.js", "/ui/assets/europe.svg"):
        a = await client.get(asset)
        assert a.status_code == 200, asset
    # bare /ui redirects into the app
    r = await client.get("/ui", follow_redirects=False)
    assert r.status_code in (301, 307, 308)


async def test_no_hard_coded_slot_ids_in_the_frontend():
    """The old UI rotted after the uuid migration (20 hard-coded p-001 paths)."""
    from pathlib import Path
    static = Path(__file__).resolve().parent.parent / "static"
    offenders = []
    for fp in list(static.glob("*.js")) + list(static.glob("**/*.js")) + [static / "index.html"]:
        text = fp.read_text(encoding="utf-8")
        for m in re.finditer(r"/(Patient|Bundle|Observation|DocumentReference)/p-\d{3}", text):
            offenders.append(f"{fp.name}: {m.group(0)}")
    assert not offenders, offenders
