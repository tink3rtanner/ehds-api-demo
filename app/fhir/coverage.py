"""Coverage: which countries have example data, from whom, and does it validate.

Feeds the UI's Coverage map. Everything here is derived from the data:

* a **submission** is one bundle in ``data/inbox/`` (the as-submitted original
  of an ITI-105 publish). Its country comes from the Patient it concerns
  (:func:`app.fhir.origin.country_of_bundle`), its category from the document
  type, its validation state from :mod:`app.fhir.validation_queue`, and its
  source from the host in ``meta.source`` / absolute ``fullUrl``s.
* the **reference** tier is the seeded panel: every DocumentReference tagged
  ``reference`` credits its patient's country with that category, drawn
  hatched on the map so ten countries are not "green" on day one.

No client ids, names or contact details appear anywhere in this output.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.config import settings
from app.fhir import store
from app.fhir import validation_queue as vq
from app.fhir.document import CATEGORY_TO_DOC_TYPE
from app.fhir.origin import (
    CATEGORY_BY_LOINC,
    COUNTRY_NAMES,
    REFERENCE,
    category_of_bundle,
    country_of_bundle,
    country_of_patient,
    is_european,
    origin_of,
)

CATEGORIES: tuple[str, ...] = tuple(CATEGORY_TO_DOC_TYPE.keys())


def _inbox_dir() -> Path:
    return settings.data_dir / "inbox"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _source_host(bundle: dict[str, Any]) -> str | None:
    src = (bundle.get("meta") or {}).get("source")
    if not src:
        for e in bundle.get("entry") or []:
            fu = e.get("fullUrl") if isinstance(e, dict) else None
            if isinstance(fu, str) and fu.startswith(("http://", "https://")):
                src = fu
                break
            res = e.get("resource") if isinstance(e, dict) else None
            ms = ((res or {}).get("meta") or {}).get("source")
            if isinstance(ms, str) and ms.startswith(("http://", "https://")):
                src = ms
                break
    if not src:
        return None
    host = urlparse(src).netloc
    return host or None


def _validation_summary(rec: dict[str, Any] | None) -> dict[str, Any]:
    if not rec:
        return {"state": "unknown"}
    return {
        "state": rec.get("state", "unknown"),
        "errors": rec.get("errors"),
        "warnings": rec.get("warnings"),
        "profile": rec.get("profile"),
        "reason": rec.get("reason"),
        "updated": rec.get("updated"),
        "attempts": rec.get("attempts"),
    }


def load_submission(inbox_id: str) -> dict[str, Any] | None:
    if "/" in inbox_id or ".." in inbox_id:
        return None
    fp = _inbox_dir() / f"{inbox_id}.json"
    try:
        return json.loads(fp.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def summarise_submission(inbox_id: str, bundle: dict[str, Any], *, mtime: float | None = None,
                         validation: dict[str, Any] | None = None) -> dict[str, Any]:
    country, how = country_of_bundle(bundle)
    types = Counter(
        (e.get("resource") or {}).get("resourceType", "?")
        for e in bundle.get("entry") or [] if isinstance(e, dict)
    )
    received = bundle.get("timestamp") or (_iso(mtime) if mtime else None)
    return {
        "id": inbox_id,
        "received": received,
        "bundle_type": bundle.get("type"),
        "category": category_of_bundle(bundle),
        "country": country,
        "country_how": how,
        "country_name": COUNTRY_NAMES.get(country or "", None),
        "european": is_european(country),
        "source_host": _source_host(bundle),
        "entry_count": sum(types.values()),
        "resource_types": dict(sorted(types.items())),
        "validation": _validation_summary(validation),
    }


def submissions() -> list[dict[str, Any]]:
    """Every inbox bundle summarised, newest first."""
    d = _inbox_dir()
    if not d.exists():
        return []
    records = vq.all_records()
    out: list[dict[str, Any]] = []
    for fp in d.glob("*.json"):
        try:
            bundle = json.loads(fp.read_text())
            mtime = fp.stat().st_mtime
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(bundle, dict):
            continue
        out.append(summarise_submission(fp.stem, bundle, mtime=mtime, validation=records.get(fp.stem)))
    out.sort(key=lambda s: s.get("received") or "", reverse=True)
    return out


def submission_resources(inbox_id: str, bundle: dict[str, Any]) -> list[str]:
    """``Type/local-id`` for every resource the store holds from this submission."""
    refs: list[str] = []
    for rtype in store.SUPPORTED_TYPES:
        for r in store.list_all(rtype):
            if origin_of(r)["submission"] == inbox_id:
                refs.append(f"{rtype}/{r['id']}")
    return sorted(refs)


def _reference_coverage() -> dict[str, set[str]]:
    """country -> categories covered by reference DocumentReferences."""
    patients = {p["id"]: p for p in store.list_all("Patient") if origin_of(p)["kind"] == REFERENCE}
    out: dict[str, set[str]] = defaultdict(set)
    for dr in store.list_all("DocumentReference"):
        if origin_of(dr)["kind"] != REFERENCE:
            continue
        pid = ((dr.get("subject") or {}).get("reference") or "").rsplit("/", 1)[-1]
        p = patients.get(pid)
        if not p:
            continue
        country = country_of_patient(p)
        if not country:
            continue
        for c in ((dr.get("type") or {}).get("coding") or []):
            cat = CATEGORY_BY_LOINC.get(c.get("code", ""))
            if cat:
                out[country].add(cat)
    return out


def coverage() -> dict[str, Any]:
    """Per-country coverage in the shape the map consumes."""
    subs = submissions()
    countries: dict[str, dict[str, Any]] = {}

    def entry(code: str) -> dict[str, Any]:
        if code not in countries:
            countries[code] = {
                "code": code,
                "name": COUNTRY_NAMES.get(code, code),
                "european": is_european(code),
                "reference": {"categories": []},
                "community": {"submissions": 0, "categories": {}, "validated": 0, "failed": 0,
                              "pending": 0, "unavailable": 0, "sources": []},
                "score": 0,
                "complete": False,
            }
        return countries[code]

    for code, cats in _reference_coverage().items():
        entry(code)["reference"]["categories"] = sorted(cats, key=CATEGORIES.index)

    unknown = 0
    validated_by_country_cat: dict[tuple[str, str], int] = Counter()
    for s in subs:
        code = s["country"]
        if not code:
            unknown += 1
            continue
        com = entry(code)["community"]
        com["submissions"] += 1
        cat = s["category"] or "other"
        com["categories"][cat] = com["categories"].get(cat, 0) + 1
        state = s["validation"]["state"]
        if state in ("validated", "failed", "pending", "unavailable"):
            com[state] += 1
        if state == "validated" and s["category"]:
            validated_by_country_cat[(code, s["category"])] += 1
        host = s["source_host"]
        if host and host not in com["sources"]:
            com["sources"].append(host)

    for code, c in countries.items():
        covered = [cat for cat in CATEGORIES if c["community"]["categories"].get(cat)]
        c["score"] = len(covered)
        c["complete"] = all(validated_by_country_cat.get((code, cat), 0) > 0 for cat in CATEGORIES)

    european = [c for c in countries.values() if c["european"]]
    return {
        "generated": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "categories": list(CATEGORIES),
        "countries": dict(sorted(countries.items())),
        "non_european": sorted(c["code"] for c in countries.values() if not c["european"]),
        "unknown_country_submissions": unknown,
        "totals": {
            "submissions": len(subs),
            "countries_with_community_data": sum(1 for c in european if c["community"]["submissions"]),
            "countries_with_reference_data": sum(1 for c in european if c["reference"]["categories"]),
            "complete_countries": sum(1 for c in european if c["complete"]),
            "validated_submissions": sum(1 for s in subs if s["validation"]["state"] == "validated"),
        },
    }
