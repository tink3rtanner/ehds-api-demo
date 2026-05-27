"""file-backed FHIR resource store.

layout: data/<dir-for-type>/<id>.json
each json file is a single FHIR resource.

design notes:
- the index is built lazily and cached in-process. mutations (writes via
  ITI-105) bust the cache.
- "type -> dir" is a small static mapping; resources go in dirs based on the
  hyphenated lowercase form of the type, with a few overrides where natural.
- searches are linear; with ~400 resources that's fine.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Iterator

from app.config import settings

# canonical FHIR-type -> dir name
_TYPE_TO_DIR: dict[str, str] = {
    "Patient": "patients",
    "Observation": "observations",
    "MedicationStatement": "medication-statements",
    "MedicationDispense": "medication-dispenses",
    "MedicationRequest": "medication-requests",
    "Medication": "medications",
    "Condition": "conditions",
    "AllergyIntolerance": "allergy-intolerances",
    "Immunization": "immunizations",
    "Procedure": "procedures",
    "DiagnosticReport": "diagnostic-reports",
    "ImagingStudy": "imaging-studies",
    "Encounter": "encounters",
    "Specimen": "specimens",
    "Practitioner": "practitioners",
    "PractitionerRole": "practitioner-roles",
    "Organization": "organizations",
    "Composition": "compositions",
    "DocumentReference": "document-references",
}

SUPPORTED_TYPES = tuple(_TYPE_TO_DIR.keys())


def dir_for_type(rtype: str) -> Path:
    if rtype not in _TYPE_TO_DIR:
        raise KeyError(f"unsupported resource type: {rtype}")
    return settings.data_dir / _TYPE_TO_DIR[rtype]


_lock = threading.Lock()
_cache: dict[str, dict[str, dict[str, Any]]] = {}


def _load_type(rtype: str) -> dict[str, dict[str, Any]]:
    """load all resources of a given type, keyed by logical id."""
    with _lock:
        if rtype in _cache:
            return _cache[rtype]
        d = dir_for_type(rtype)
        out: dict[str, dict[str, Any]] = {}
        if d.exists():
            for fp in sorted(d.glob("*.json")):
                try:
                    res = json.loads(fp.read_text())
                except json.JSONDecodeError:
                    continue
                rid = res.get("id") or fp.stem
                out[rid] = res
        _cache[rtype] = out
        return out


def invalidate_cache(rtype: str | None = None) -> None:
    with _lock:
        if rtype is None:
            _cache.clear()
        else:
            _cache.pop(rtype, None)


def read(rtype: str, rid: str) -> dict[str, Any] | None:
    return _load_type(rtype).get(rid)


def list_all(rtype: str) -> Iterator[dict[str, Any]]:
    yield from _load_type(rtype).values()


def write(resource: dict[str, Any]) -> dict[str, Any]:
    rtype = resource["resourceType"]
    rid = resource["id"]
    d = dir_for_type(rtype)
    d.mkdir(parents=True, exist_ok=True)
    fp = d / f"{rid}.json"
    fp.write_text(json.dumps(resource, indent=2, sort_keys=True))
    invalidate_cache(rtype)
    return resource


# ---------- search helpers ----------

def _walk(obj: Any) -> Iterator[Any]:
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _resource_refs_patient(res: dict[str, Any]) -> str | None:
    """find the Patient/<id> reference for a resource via the canonical fields."""
    for key in ("subject", "patient", "beneficiary"):
        ref = res.get(key)
        if isinstance(ref, dict) and isinstance(ref.get("reference"), str):
            r = ref["reference"]
            if r.startswith("Patient/"):
                return r.split("/", 1)[1]
    return None


def _match_token(res: dict[str, Any], field: str, value: str) -> bool:
    """rudimentary token search: status, code, category fields. value can be system|code."""
    target = value.split("|")[-1]  # strip system prefix
    fv = res.get(field)
    if isinstance(fv, str):
        return fv == value or fv == target
    if isinstance(fv, dict):
        for c in _walk(fv):
            if isinstance(c, dict) and (c.get("code") == target or c.get("value") == target):
                return True
        return False
    if isinstance(fv, list):
        for item in fv:
            for c in _walk(item):
                if isinstance(c, dict) and (c.get("code") == target or c.get("value") == target):
                    return True
    return False


def search(rtype: str, params: dict[str, list[str]]) -> list[dict[str, Any]]:
    """tiny generic search; resource-specific routers may layer additional logic."""
    results = list(list_all(rtype))

    def take(name: str) -> str | None:
        v = params.get(name)
        return v[0] if v else None

    # _id
    if (rid := take("_id")):
        results = [r for r in results if r.get("id") == rid]

    # patient compartment (works for any subject-based resource)
    if (pat := take("patient")):
        pid = pat.split("/")[-1]
        results = [r for r in results if _resource_refs_patient(r) == pid]

    # generic identifier (Patient + Practitioner + Organization)
    if (ident := take("identifier")):
        wanted = ident.split("|")[-1]
        def has_ident(r):
            for i in r.get("identifier", []) or []:
                if i.get("value") == wanted:
                    return True
            return False
        results = [r for r in results if has_ident(r)]

    # status / category / code as token-ish
    for tok in ("status", "clinical-status", "category", "code", "intent"):
        if (val := take(tok)) is not None:
            field = "clinicalStatus" if tok == "clinical-status" else tok
            results = [r for r in results if _match_token(r, field, val)]

    return results


def bundle_searchset(rtype: str, entries: list[dict[str, Any]], base_url: str | None = None) -> dict[str, Any]:
    base = base_url or settings.base_url
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": len(entries),
        "entry": [
            {
                "fullUrl": f"{base}/{rtype}/{e['id']}",
                "resource": e,
                "search": {"mode": "match"},
            }
            for e in entries
        ],
    }


def all_referenced_resources_for_patient(pid: str) -> list[dict[str, Any]]:
    """gather every resource that references Patient/<pid> across all supported types."""
    found: list[dict[str, Any]] = []
    for rtype in SUPPORTED_TYPES:
        if rtype == "Patient":
            p = read("Patient", pid)
            if p:
                found.append(p)
            continue
        for r in list_all(rtype):
            if _resource_refs_patient(r) == pid:
                found.append(r)
            else:
                # also catch references in performer/recorder etc.
                for sub in _walk(r):
                    if isinstance(sub, dict) and sub.get("reference") == f"Patient/{pid}":
                        found.append(r)
                        break
    return found
