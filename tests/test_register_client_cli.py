"""smoke test for the register_client CLI tool.

invoked via subprocess so test env vars don't leak into the in-process app
configuration used by other tests.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_register_client_with_generate(tmp_path):
    registry = tmp_path / "clients.json"
    keys_dir = tmp_path / "keys"
    keys_dir.mkdir()

    env = {**os.environ,
           "EHDS_CLIENT_REGISTRY": str(registry),
           "EHDS_JWKS_PATH": str(keys_dir),
           "PYTHONPATH": str(REPO_ROOT)}
    r = subprocess.run(
        [sys.executable, "-m", "app.tools.register_client",
         "--client-id", "test-x", "--generate",
         "--scope", "system/*.read", "--scope", "system/Bundle.write"],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, f"stdout={r.stdout}\nstderr={r.stderr}"
    assert registry.exists()
    data = json.loads(registry.read_text())
    assert any(c["client_id"] == "test-x" for c in data["clients"])
    assert (tmp_path / "client-test-x.pem").exists()
    assert (tmp_path / "client-test-x.pub.pem").exists()
