"""Browser smoke test for the UI: every route renders without console errors,
at desktop and phone width, against a real server process.

Needs playwright + a chromium. Skips cleanly when either is missing so the
fast suite stays runnable everywhere; CI installs both (see .github/workflows).
Set EHDS_CHROMIUM=/path/to/chrome to use an existing binary.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api", reason="playwright not installed")

REPO_ROOT = Path(__file__).resolve().parent.parent
ROUTES = ["#/", "#/scenario/0", "#/patients", "#/documents", "#/coverage", "#/connect", "#/activity"]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server_url():
    """uvicorn on a free port, inheriting the test env from conftest (temp data dir)."""
    port = _free_port()
    env = {**os.environ, "EHDS_BASE_URL": f"http://127.0.0.1:{port}", "EHDS_ISSUER": f"http://127.0.0.1:{port}"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=REPO_ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        import urllib.request
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1).read()
                break
            except Exception:  # noqa: BLE001
                if proc.poll() is not None:
                    raise RuntimeError(proc.stdout.read().decode(errors="replace"))
                time.sleep(0.3)
        else:
            proc.kill()
            raise RuntimeError("server did not start")
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        kwargs = {}
        if os.environ.get("EHDS_CHROMIUM"):
            kwargs["executable_path"] = os.environ["EHDS_CHROMIUM"]
        try:
            b = p.chromium.launch(**kwargs)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no chromium available for playwright: {str(e).splitlines()[0]}")
        yield b
        b.close()


def _collect(page):
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console.error: {m.text}") if m.type == "error" else None)
    return errors


@pytest.mark.parametrize("width", [1440, 390])
def test_every_route_renders_without_errors(server_url, browser, width):
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    page = ctx.new_page()
    errors = _collect(page)
    try:
        for route in ROUTES:
            errors.clear()
            page.goto(f"{server_url}/ui/{route}")
            page.wait_for_timeout(2500)
            assert not errors, f"{route} @ {width}px: {errors}"
            # the page shell rendered something real, not the loading spinner
            text = page.evaluate("() => document.getElementById('app').innerText")
            assert len(text) > 200, f"{route} @ {width}px rendered almost nothing"
            assert "Loading…" not in text[:40], f"{route} @ {width}px stuck loading"
            # no horizontal overflow (design goal 6: phone is a real target)
            sw = page.evaluate("() => document.documentElement.scrollWidth")
            assert sw <= width + 1, f"{route} @ {width}px overflows horizontally ({sw}px)"
    finally:
        ctx.close()


def test_patient_and_document_pages_render(server_url, browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    errors = _collect(page)
    try:
        page.goto(f"{server_url}/ui/#/patients")
        page.wait_for_timeout(2500)
        href = page.evaluate("() => document.querySelector('a[href^=\"#/patients/\"]').getAttribute('href')")
        page.goto(f"{server_url}/ui/{href}")
        page.wait_for_timeout(3500)
        text = page.evaluate("() => document.getElementById('app').innerText")
        assert "Reference" in text and "Timeline" in text and "Compartment" in text
        page.goto(f"{server_url}/ui/#/documents")
        page.wait_for_timeout(2500)
        dhref = page.evaluate("() => document.querySelector('a[href^=\"#/documents/\"]').getAttribute('href')")
        page.goto(f"{server_url}/ui/{dhref}")
        page.wait_for_timeout(3000)
        text = page.evaluate("() => document.getElementById('app').innerText")
        assert "Patient Summary" in text and "entries" in text
        assert not errors, errors
    finally:
        ctx.close()


def test_scenario_walks_all_six_steps_with_a_real_smart_client(server_url, browser):
    """Step 1 registers a browser-side client and mints a token via /token;
    steps 2-5 use that bearer; step 6 shows the audit receipt for the run."""
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    errors = _collect(page)
    try:
        page.goto(f"{server_url}/ui/#/scenario/0")
        seen = []
        for step in range(6):
            page.wait_for_timeout(3500)
            seen.append(page.evaluate("() => document.querySelector('.scene-result')?.innerText || ''"))
            if step < 5:
                page.click("text=Next")
        assert "registered its public half" in seen[0] or "registered as client" in seen[0], seen[0][:200]
        assert "candidate" in seen[1] and "certain" in seen[1], seen[1][:200]
        assert "documents are registered" in seen[2], seen[2][:200]
        assert "Bundle.type=document" in seen[3], seen[3][:200]
        assert "allergies" in seen[4], seen[4][:200]
        assert "/token" in seen[5] and "/Patient/$match" in seen[5], seen[5][:300]
        assert not errors, errors
    finally:
        ctx.close()


def test_request_drawer_opens_from_a_chip(server_url, browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    try:
        page.goto(f"{server_url}/ui/#/connect")
        page.wait_for_timeout(2500)
        # the endpoint reference's first chip is `GET /` (discovery); POST chips run forms instead
        page.click(".endpoint-row .req-chip >> nth=0")
        page.wait_for_timeout(1500)
        assert page.evaluate("() => !document.querySelector('.drawer').hidden")
        assert "HTTP 200" in page.evaluate("() => document.querySelector('.drawer').innerText")
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        assert page.evaluate("() => document.querySelector('.drawer').hidden")
    finally:
        ctx.close()
