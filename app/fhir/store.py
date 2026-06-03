"""file-backed FHIR resource store.

layout: data/<dir-for-type>/<id>.json
each json file is a single FHIR resource.

design notes:
- the index is built lazily and cached in-process. each cache entry is tagged
  with a cheap signature of the on-disk type-dir (file count + newest mtime);
  every read re-stats the dir and reloads when the signature changed. this
  keeps the cache correct ACROSS PROCESSES: under gunicorn the service runs
  multiple pre-forked workers, each with its own `_cache`, so a write handled
  by one worker would otherwise be invisible to the others until restart. the
  signature check means any worker notices another worker's add/update/delete
  on its next read. `invalidate_cache` remains as an in-process fast-path.
- "type -> dir" is a small static mapping; resources go in dirs based on the
  hyphenated lowercase form of the type, with a few overrides where natural.
- searches are linear; with ~400 resources that's fine.
"""
from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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
# rtype -> (dir-signature, {id: resource}). the signature lets a worker detect
# on-disk changes made by ANOTHER worker (or out-of-band) and reload.
_cache: dict[str, tuple[tuple[int, int], dict[str, dict[str, Any]]]] = {}


def _dir_signature(d: Path) -> tuple[int, int]:
    """cheap fingerprint of a type-dir: (count of *.json, newest mtime_ns).

    an add bumps the count, a delete drops it, and any add/update sets a file
    mtime to "now" which exceeds the previously-recorded newest — so any
    mutation changes the signature. one scandir + stat-per-entry, no file
    reads, so it is cheap to run on every access.
    """
    count = 0
    newest = 0
    try:
        with os.scandir(d) as it:
            for entry in it:
                if not entry.name.endswith(".json"):
                    continue
                count += 1
                try:
                    mtime = entry.stat().st_mtime_ns
                except OSError:
                    continue
                if mtime > newest:
                    newest = mtime
    except FileNotFoundError:
        pass
    return (count, newest)


def _load_type(rtype: str) -> dict[str, dict[str, Any]]:
    """load all resources of a given type, keyed by logical id."""
    d = dir_for_type(rtype)
    sig = _dir_signature(d)
    with _lock:
        cached = _cache.get(rtype)
        if cached is not None and cached[0] == sig:
            return cached[1]
        out: dict[str, dict[str, Any]] = {}
        if d.exists():
            for fp in sorted(d.glob("*.json")):
                try:
                    res = json.loads(fp.read_text())
                except json.JSONDecodeError:
                    continue
                rid = res.get("id") or fp.stem
                out[rid] = res
        _cache[rtype] = (sig, out)
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


def resolve_patient_ref(value: str) -> str | None:
    """resolve a search-param value to a canonical Patient.id (uuid).

    Accepts:
      - a Patient.id (uuid) directly
      - a slot identifier (Patient.identifier with the demo slot system),
        e.g. ``p-001``
      - a full reference like ``Patient/<id-or-slot>``
    Returns the canonical Patient.id or None if no patient matches.
    """
    # strip Type/ prefix if given
    if value.startswith("Patient/"):
        value = value.split("/", 1)[1]
    # exact id match first (cheapest)
    p = read("Patient", value)
    if p is not None:
        return p["id"]
    # then try Patient.identifier value lookup (any system)
    for p in list_all("Patient"):
        for ident in p.get("identifier", []) or []:
            if ident.get("value") == value:
                return p["id"]
    return None


def find_patient_ids_by_identifier(value: str) -> set[str]:
    """find Patient.ids whose identifier matches a FHIR token search value.

    accepts the standard FHIR token form:
      - ``system|value``  -> both must match (strict, recommended)
      - ``|value``        -> system must be absent (rare)
      - ``value``         -> bare match against any system (loose)
    """
    if "|" in value:
        wanted_system, wanted_value = value.split("|", 1)
    else:
        wanted_system, wanted_value = None, value
    out: set[str] = set()
    for p in list_all("Patient"):
        for ident in p.get("identifier", []) or []:
            if ident.get("value") != wanted_value:
                continue
            if wanted_system is not None and ident.get("system") != wanted_system:
                continue
            out.add(p["id"])
            break
    return out


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


# ---------- chained-search building blocks ----------
# These generalise the one-off `patient.identifier` chaining above so any MHD
# ITI-67 search parameter that is really a property of a *referenced* clinical
# resource (author -> Practitioner, context.encounter -> Encounter) can be
# resolved by walking the reference graph at query time, rather than
# denormalising the value onto the DocumentReference. See
# docs/document-search-chaining.md.

def resolve_reference(ref: str | None) -> dict[str, Any] | None:
    """Load the target of a literal ``Type/id`` reference from the store.

    Tolerates absolute references (``http://host/Type/id``) and bare relative
    ones (``Type/id``) by taking the trailing two path segments. Returns None
    when the reference is unparseable, points at an unsupported type, or the
    target is not held locally.
    """
    if not isinstance(ref, str) or "/" not in ref:
        return None
    parts = ref.rstrip("/").split("/")
    rtype, rid = parts[-2], parts[-1]
    if rtype not in _TYPE_TO_DIR:
        return None
    return read(rtype, rid)


def token_in(value: Any, wanted: str) -> bool:
    """True if a FHIR token search value matches anywhere in ``value``.

    ``value`` may be a Coding, a CodeableConcept, a code string, or a list of
    any of those. ``wanted`` is ``code`` or ``system|code`` — the system half is
    accepted but only the code is matched (the demo never collides on code).
    """
    target = wanted.split("|")[-1]
    if value is None:
        return False
    if isinstance(value, str):
        return value == target
    for c in _walk(value):
        if isinstance(c, dict) and (c.get("code") == target or c.get("value") == target):
            return True
    return False


_DATE_PREFIXES = ("eq", "ne", "gt", "lt", "ge", "le", "sa", "eb", "ap")


def match_date(query: str, target: str | None) -> bool:
    """FHIR ``date`` parameter match against an ISO-8601 instant/date/dateTime.

    Honours the comparison prefixes (eq/ne/gt/lt/ge/le/sa/eb/ap). Comparison is
    lexical on the ISO string, which is order-correct for the consistently
    formatted timestamps this server emits. ``eq``/``ne`` compare on the shared
    prefix so a day-granularity query (``2024-04-15``) matches a dateTime that
    starts with it. ``ap`` is treated as ``eq`` (approximate ≈ same instant).
    """
    if not target or not query:
        return False
    prefix, val = "eq", query
    if len(query) >= 2 and query[:2] in _DATE_PREFIXES:
        prefix, val = query[:2], query[2:]
    if prefix in ("eq", "ap"):
        return target.startswith(val)
    if prefix == "ne":
        return not target.startswith(val)
    if prefix in ("gt", "sa"):
        return target > val
    if prefix in ("lt", "eb"):
        return target < val
    if prefix == "ge":
        return target >= val
    if prefix == "le":
        return target <= val
    return target.startswith(val)


def search(rtype: str, params: dict[str, list[str]]) -> list[dict[str, Any]]:
    """tiny generic search; resource-specific routers may layer additional logic."""
    results = list(list_all(rtype))

    def take(name: str) -> str | None:
        v = params.get(name)
        return v[0] if v else None

    # _id
    if (rid := take("_id")):
        results = [r for r in results if r.get("id") == rid]

    # patient compartment. supports these forms (FHIR-canonical + MHD-canonical):
    #   ?patient=<uuid|slot|Patient/x>      direct reference resolution
    #   ?patient.identifier=<system|value>  FHIR chained search (MHD ITI-67)
    #   ?patient:identifier=<system|value>  FHIR ':identifier' modifier
    #   ?patient=<system>|<value>           identifier-token shorthand on
    #                                       a reference param (HAPI-style)
    pat_ident = (take("patient.identifier")
                 or take("patient:identifier"))
    if not pat_ident:
        pat = take("patient")
        if pat and "|" in pat:
            pat_ident, pat = pat, None
        if pat_ident is None and pat:
            canonical = resolve_patient_ref(pat)
            if canonical is None:
                return []
            results = [r for r in results if _resource_refs_patient(r) == canonical]
    if pat_ident:
        matches = find_patient_ids_by_identifier(pat_ident)
        if not matches:
            return []
        results = [r for r in results if _resource_refs_patient(r) in matches]

    # FHIR identifier search — `system|value` form preferred (system-qualified).
    # bare `value` is accepted but a recipe for collisions; we still match it
    # against any identifier with that value regardless of system.
    if (ident := take("identifier")):
        if "|" in ident:
            wanted_system, wanted_value = ident.split("|", 1)
        else:
            wanted_system, wanted_value = None, ident
        def has_ident(r):
            for i in r.get("identifier", []) or []:
                if i.get("value") != wanted_value:
                    continue
                if wanted_system and i.get("system") != wanted_system:
                    continue
                return True
            return False
        results = [r for r in results if has_ident(r)]

    # status / category / code as token-ish
    for tok in ("status", "clinical-status", "category", "code", "intent"):
        if (val := take(tok)) is not None:
            field = "clinicalStatus" if tok == "clinical-status" else tok
            results = [r for r in results if _match_token(r, field, val)]

    return results


def bundle_searchset(rtype: str, entries: list[dict[str, Any]], base_url: str | None = None,
                     self_link: str | None = None) -> dict[str, Any]:
    base = base_url or settings.base_url
    out: dict[str, Any] = {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": len(entries),
    }
    # `self` link — the request URL that produced this searchset (FHIR searchset
    # convention / White Paper base-scenario shape). Callers pass str(request.url).
    if self_link:
        out["link"] = [{"relation": "self", "url": self_link}]
    out["entry"] = [
        {
            "fullUrl": f"{base}/{rtype}/{e['id']}",
            "resource": e,
            "search": {"mode": "match"},
        }
        for e in entries
    ]
    return out


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
