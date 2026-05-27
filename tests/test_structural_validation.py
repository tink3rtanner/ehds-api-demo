"""fast structural validation: every resource on disk + every compiled doc
parses cleanly through fhir.resources pydantic."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.fhir.validate import structural_validate
from scripts.seed import PANEL


REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / "data"
CATEGORIES = ["patient-summary", "laboratory-report", "discharge-report", "imaging-report"]


def _all_resource_files() -> list[Path]:
    out: list[Path] = []
    for sub in DATA.iterdir():
        if sub.name == "inbox" or not sub.is_dir():
            continue
        out.extend(sub.glob("*.json"))
    return out


@pytest.mark.parametrize("path", _all_resource_files(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_atomic_resource_is_structurally_valid(path):
    res = json.loads(path.read_text())
    ok, problems = structural_validate(res)
    assert ok, f"{path}: {problems[:1]}"


@pytest.mark.asyncio
@pytest.mark.parametrize("category", CATEGORIES)
async def test_compiled_documents_are_structurally_valid(client, auth_headers, category):
    for p in PANEL:
        r = await client.get(f"/Binary/doc-{p.pid}-{category}", headers=auth_headers)
        assert r.status_code == 200, r.text
        bundle = r.json()
        ok, problems = structural_validate(bundle)
        assert ok, f"{p.pid}/{category}: {problems[:2]}"
