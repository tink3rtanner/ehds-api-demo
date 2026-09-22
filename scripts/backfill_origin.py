"""One-off migration: tag every stored resource with its origin.

Resources written before origin tagging existed (see app/fhir/origin.py) have
no ``urn:ehds-demo:origin`` tag. The reference panel is fixed by re-running
``scripts.seed``; this script handles everything else in the store:

* every untagged resource is ``community``: the seed script tags everything
  it writes as ``reference`` and nothing else ever lands in the store except
  through an ingest boundary (ITI-105 submit, Epic import) — so run the seed
  first, then this
* if an inbox bundle contains an entry whose id (or fullUrl) matches the
  resource's ``urn:ehds-demo:source-id`` identifier, the submission tag is
  added too, so the UI can link the resource back to the bundle it came in with
* resources with neither ``meta.source`` nor a source-id are still tagged
  community but reported, so you can see what has no provenance at all

Idempotent. Safe to run on the live data dir while the service is up: the
store re-reads a type dir when its signature changes.

usage:  python -m scripts.backfill_origin [--data-dir path] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path

os.environ.setdefault("EHDS_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data"))

from app.config import settings  # noqa: E402
from app.fhir import store  # noqa: E402
from app.fhir.naturalize import SOURCE_ID_SYSTEM, _local_id  # noqa: E402
from app.fhir.origin import COMMUNITY, origin_of, tag_origin  # noqa: E402


def _source_ids(res: dict) -> list[str]:
    idents = res.get("identifier")
    if isinstance(idents, dict):  # a malformed but seen-in-the-wild shape
        idents = [idents]
    return [i.get("value") for i in idents or []
            if isinstance(i, dict) and i.get("system") == SOURCE_ID_SYSTEM and i.get("value")]


def _inbox_index(inbox: Path) -> dict[str, str]:
    """Keys that identify an entry of an inbox bundle -> submission id.

    Three keys per entry: the foreign id, the fullUrl, and the *local* id the
    naturalizer minted for it (deterministic uuid5 of the origin key), so a
    stored resource can be matched by its own id even when it carries no
    source-id identifier.
    """
    idx: dict[str, str] = {}
    if not inbox.exists():
        return idx
    for fp in inbox.glob("*.json"):
        try:
            b = json.loads(fp.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for e in b.get("entry") or []:
            r = e.get("resource") if isinstance(e, dict) else None
            if not isinstance(r, dict):
                continue
            full_url = e.get("fullUrl") if isinstance(e.get("fullUrl"), str) else None
            if r.get("id"):
                idx.setdefault(r["id"], fp.stem)
            if full_url:
                idx.setdefault(full_url, fp.stem)
            origin_key = full_url or (f"{r.get('resourceType')}/{r['id']}" if r.get("id") else None)
            if origin_key:
                idx.setdefault(_local_id(origin_key), fp.stem)
    return idx


def backfill(data_dir: Path, *, dry_run: bool = False) -> Counter:
    inbox_idx = _inbox_index(data_dir / "inbox")
    stats: Counter = Counter()
    for rtype in store.SUPPORTED_TYPES:
        for res in list(store.list_all(rtype)):
            o = origin_of(res)
            if o["kind"] == "reference" or (o["kind"] == "community" and o["submission"]):
                stats["already-tagged"] += 1
                continue
            sids = _source_ids(res)
            submission = next((inbox_idx[k] for k in [res.get("id"), *sids] if k in inbox_idx), None)
            if o["kind"] == "community" and not submission:
                stats["already-tagged"] += 1  # community, and still nothing to link it to
                continue
            if not sids and not (res.get("meta") or {}).get("source") and not submission:
                stats["no-provenance"] += 1
                print(f"  ? {rtype}/{res['id']} has neither meta.source, a source-id nor an inbox match (tagged community anyway)")
            tag_origin(res, COMMUNITY, submission_id=submission)
            stats["tagged-community"] += 1
            stats["linked-to-submission" if submission else "no-submission-match"] += 1
            if not dry_run:
                store.write(res)
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data-dir", default=str(settings.data_dir))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    stats = backfill(Path(args.data_dir), dry_run=args.dry_run)
    for k, v in sorted(stats.items()):
        print(f"{k:22s} {v}")
    if args.dry_run:
        print("(dry run — nothing written)")


if __name__ == "__main__":
    main()
