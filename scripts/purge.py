"""Remove published (community) data, after taking a backup.

Seeded reference data is never touched. Dry run unless --apply.

    python -m scripts.purge --submission <id>          # one publish: its DocumentReference,
                                                         # stored bytes, inbox copy, validation
                                                         # record and the resources it brought
    python -m scripts.purge --all-community            # every published thing
    python -m scripts.purge --all-community --apply    # do it

Every --apply first writes backups/data-<timestamp>.tar.gz (the whole data/
directory minus the validator cache), so a purge can be undone with
``tar -xzf backups/data-<ts>.tar.gz``. Restart the service afterwards
(``sudo systemctl restart ehds-api``) so nothing is served from memory.
"""
from __future__ import annotations

import argparse
import json
import tarfile
import time
from pathlib import Path

from app.config import settings
from app.fhir import store
from app.fhir.origin import COMMUNITY, origin_of

BACKUP_DIR = Path(__file__).resolve().parent.parent / "backups"


def backup() -> Path:
    BACKUP_DIR.mkdir(exist_ok=True)
    out = BACKUP_DIR / f"data-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz"
    with tarfile.open(out, "w:gz") as tar:
        tar.add(settings.data_dir, arcname="data",
                filter=lambda ti: None if "/validator-home" in ti.name else ti)
    return out


def plan(submission: str | None) -> list[Path]:
    """Every file to delete."""
    files: list[Path] = []
    for rtype in store.SUPPORTED_TYPES:
        for res in store.list_all(rtype):
            o = origin_of(res)
            if o["kind"] != COMMUNITY:
                continue
            if submission and o["submission"] != submission:
                continue
            files.append(store.dir_for_type(rtype) / f"{res['id']}.json")
    d = settings.data_dir
    if submission:
        names = [d / "inbox" / f"{submission}.json", d / "documents" / f"{submission}.bin",
                 d / "validation" / f"{submission}.json"]
        files += [p for p in names if p.exists()]
    else:
        files += sorted((d / "inbox").glob("*.json")) + sorted((d / "documents").glob("*.bin"))
        for p in sorted((d / "validation").glob("*.json")):
            try:
                if json.loads(p.read_text()).get("kind") == "submission":
                    files.append(p)
            except ValueError:
                pass
    return [p for p in dict.fromkeys(files) if p.exists()]


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--submission", help="the id of one publish (the DocumentReference id for ITI-105)")
    g.add_argument("--all-community", action="store_true", help="every published (non-seeded) thing")
    ap.add_argument("--apply", action="store_true", help="delete (default: dry run)")
    args = ap.parse_args()

    files = plan(args.submission)
    by_dir: dict[str, int] = {}
    for p in files:
        by_dir[p.parent.name] = by_dir.get(p.parent.name, 0) + 1
    print(f"{len(files)} files: " + (", ".join(f"{k} {v}" for k, v in sorted(by_dir.items())) or "nothing to do"))
    if not args.apply or not files:
        if files:
            print("dry run; add --apply to delete (a backup is written first)")
        return 0
    print(f"backup: {backup()}")
    for p in files:
        p.unlink()
    store.invalidate_cache()
    print("deleted. restart the service: sudo systemctl restart ehds-api")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
