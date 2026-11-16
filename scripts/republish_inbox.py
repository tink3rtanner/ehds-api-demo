"""Republish older ``POST /`` uploads as ITI-105 documents.

Before ``POST /DocumentReference`` existed, the Epic pipeline uploaded its
documents as transaction Bundles to ``POST /``. Their resources were stored, but
no DocumentReference was made, so ITI-67 never found them. This script turns
each distinct one into a ``Bundle.type=document`` and publishes it through the
same code path as ``POST /DocumentReference``.

Nothing is deleted. The inbox originals stay as they are. Duplicate uploads
(the same Composition sent several times) are published once, newest first.
Publishing is idempotent across runs: a Composition already published is skipped.

    python -m scripts.republish_inbox            # dry run: list what would be published
    python -m scripts.republish_inbox --apply    # publish
"""
from __future__ import annotations

import argparse
import base64
import json
import uuid
from datetime import UTC, datetime

from app.config import settings
from app.fhir import store
from app.fhir.ids import EHDS_NAMESPACE
from app.fhir.origin import EPIC_SANDBOX_TAG, category_of_bundle

REPUBLISH_SYSTEM = "urn:ehds-demo:republished-from-inbox"
# resources the Epic transform mints a fresh id for on every run, so they do not
# tell two uploads apart
_MINTED_PER_UPLOAD = {"Composition", "MedicationStatement"}


def _composition(bundle: dict) -> dict | None:
    return next((e.get("resource") for e in bundle.get("entry") or []
                 if (e.get("resource") or {}).get("resourceType") == "Composition"), None)


def _is_epic(bundle: dict) -> bool:
    tags = (bundle.get("meta") or {}).get("tag") or []
    return any("epic" in (t.get("code") or "") or "epic.com" in (t.get("system") or "") for t in tags) or \
        "epic.com" in ((bundle.get("meta") or {}).get("source") or "")


def as_document(bundle: dict, inbox_id: str, mtime: float) -> dict:
    """The upload as a document Bundle: Composition first, no request elements."""
    comp = _composition(bundle)
    entries = [{k: v for k, v in e.items() if k != "request"} for e in bundle.get("entry") or []]
    entries.sort(key=lambda e: (e.get("resource") or {}).get("resourceType") != "Composition")
    # Bundle.timestamp is an instant; a date-only Composition.date cannot be used
    ts = comp.get("date") if "T" in (comp.get("date") or "") else \
        datetime.fromtimestamp(mtime, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc = {
        "resourceType": "Bundle",
        "id": str(uuid.uuid5(EHDS_NAMESPACE, f"republished/{inbox_id}")),
        "meta": {k: v for k, v in (bundle.get("meta") or {}).items() if k in ("profile", "source", "tag")},
        "identifier": {"system": "urn:ietf:rfc:3986",
                       "value": f"urn:uuid:{uuid.uuid5(EHDS_NAMESPACE, f'republished-identifier/{inbox_id}')}"},
        "type": "document",
        "timestamp": ts,
        "entry": entries,
    }
    if not doc["meta"]:
        doc.pop("meta")
    return doc


def candidates() -> list[tuple[str, dict, float]]:
    """(inbox id, bundle, mtime) for every upload with a Composition, one per
    distinct Composition, newest first."""
    inbox = settings.data_dir / "inbox"
    seen: set[str] = set()
    out = []
    for fp in sorted(inbox.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            b = json.loads(fp.read_text())
        except ValueError:
            continue
        if b.get("type") != "transaction" or _composition(b) is None or category_of_bundle(b) is None:
            continue
        # the same document uploaded again differs only in freshly minted ids;
        # key on the category plus the source-identified resources it carries
        key = json.dumps([category_of_bundle(b), sorted(
            f"{(e.get('resource') or {}).get('resourceType')}/{(e.get('resource') or {}).get('id')}"
            for e in b.get("entry") or []
            if (e.get("resource") or {}).get("resourceType") not in _MINTED_PER_UPLOAD)])
        if key in seen:
            continue
        seen.add(key)
        out.append((fp.stem, b, fp.stat().st_mtime))
    return out


def already_published(inbox_id: str) -> bool:
    return any(i.get("system") == REPUBLISH_SYSTEM and i.get("value") == inbox_id
               for d in store.list_all("DocumentReference") for i in d.get("identifier") or [])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="publish (default is a dry run)")
    args = ap.parse_args()
    from app.routers.publish import publish

    for inbox_id, bundle, mtime in candidates():
        cat = category_of_bundle(bundle)
        if already_published(inbox_id):
            print(f"skip   {inbox_id}  {cat}: already republished")
            continue
        if not args.apply:
            print(f"would  {inbox_id}  {cat}  ({len(bundle.get('entry') or [])} entries)")
            continue
        doc = as_document(bundle, inbox_id, mtime)
        raw = json.dumps(doc, indent=2, ensure_ascii=False).encode()
        docref = {
            "resourceType": "DocumentReference",
            "status": "current",
            "identifier": [{"system": REPUBLISH_SYSTEM, "value": inbox_id}],
            "type": _composition(bundle).get("type"),
            "description": "Republished from an earlier POST / upload",
            "content": [{"attachment": {"contentType": "application/fhir+json",
                                        "data": base64.b64encode(raw).decode()}}],
        }
        codes = [c.get("code") for c in (docref["type"] or {}).get("coding") or []]
        if "34105-7" in codes and "18842-5" not in codes:
            # the IG searches discharge reports by 18842-5; HDR fixes 34105-7 on the Composition
            docref["type"] = {"coding": [*docref["type"]["coding"],
                                         {"system": "http://loinc.org", "code": "18842-5", "display": "Discharge summary"}]}
        if _is_epic(bundle):
            docref["meta"] = {"tag": [dict(EPIC_SANDBOX_TAG)]}
        resp = publish(docref)
        body = json.loads(resp.body)
        if resp.status_code == 201:
            print(f"ok     {inbox_id}  {cat} -> DocumentReference/{body['id']} ({body['subject']['reference']})")
        else:
            print(f"FAIL   {inbox_id}  {cat}: {resp.status_code} {body}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
