"""Naturalize foreign FHIR resources into this server's local identity space
while preserving a resolvable path back to the source.

Every ingest boundary (the raw ITI-105 `POST /` submit handler, and the Epic
`/Epic/$import` pipeline) should run incoming resources through this so the
server holds ONE consistent identity space (deterministic local uuid5 ids,
internal references rewritten to point at those ids) — instead of trusting
whatever ids a foreign system happened to mint.

"Preserve a path back to source" is the other half: naturalizing must never be
a lossy one-way trip. For each resource we keep two back-links:

  * an **origin business identifier** (`urn:ehds-demo:source-id` | <foreign id>)
    so the original id is searchable and survives reference-rewriting, and
  * **`meta.source`** = the absolute URL the resource was fetched from / would
    be fetched from at the source. That is the literal, clickable link a viewer
    follows to "open at source" — see the `$source` operation in
    `app/routers/source_link.py`. For Epic-sourced data this resolves against
    the Epic FHIR REST API today (there is no Epic MHD endpoint, so the back
    link is resource-level FHIR REST, not document-level ITI-67/68).

This is the White Paper "Approach #2" (persistent local copy WITH provenance),
not "Approach #1" (store foreign ids verbatim — which drifts and dangles).
"""
from __future__ import annotations

import copy
import uuid
from typing import Any

from app.fhir.ids import EHDS_NAMESPACE
from app.fhir.store import SUPPORTED_TYPES

# business-identifier system under which we stash a naturalized resource's
# original (foreign) logical id, so `?identifier=urn:ehds-demo:source-id|<id>`
# finds it and the origin survives the reference-rewriting pass.
SOURCE_ID_SYSTEM = "urn:ehds-demo:source-id"


def set_source(res: dict[str, Any], source_url: str | None) -> None:
    """Stamp meta.source with the absolute origin URL (the clickable back-link)."""
    if source_url:
        res.setdefault("meta", {})["source"] = source_url


def add_source_identifier(res: dict[str, Any], value: str,
                          system: str = SOURCE_ID_SYSTEM) -> None:
    """Preserve a foreign business/logical id as a searchable identifier."""
    idents = res.get("identifier")
    if not isinstance(idents, list):
        idents = []
    if not any(i.get("system") == system and i.get("value") == value for i in idents):
        idents.append({"system": system, "value": value})
    res["identifier"] = idents


def _rewrite_refs(obj: Any, mapping: dict[str, str]) -> None:
    """In-place: rewrite every {"reference": old} where old is in mapping."""
    if isinstance(obj, dict):
        ref = obj.get("reference")
        if isinstance(ref, str) and ref in mapping:
            obj["reference"] = mapping[ref]
        for v in obj.values():
            _rewrite_refs(v, mapping)
    elif isinstance(obj, list):
        for v in obj:
            _rewrite_refs(v, mapping)


def _demote_dangling_urn_refs(obj: Any) -> None:
    """In-place: any `reference` still pointing at a `urn:uuid:` after rewriting
    is dangling (its target wasn't a supported entry in the bundle). Demote it to
    a **logical reference** — move the urn into `Reference.identifier` and drop
    the unresolvable literal `reference` — per the naturalization recipe, so the
    store never holds a literal reference that resolves to nothing on this host.
    """
    if isinstance(obj, dict):
        ref = obj.get("reference")
        if isinstance(ref, str) and ref.startswith("urn:uuid:"):
            obj.pop("reference")
            obj.setdefault("identifier", {"system": "urn:ietf:rfc:3986", "value": ref})
        for v in obj.values():
            _demote_dangling_urn_refs(v)
    elif isinstance(obj, list):
        for v in obj:
            _demote_dangling_urn_refs(v)


def _local_id(origin_key: str | None) -> str:
    """Deterministic local id from a stable origin key (idempotent re-ingest),
    or a random uuid4 when the source gave us nothing stable to hash."""
    if origin_key:
        return str(uuid.uuid5(EHDS_NAMESPACE, f"submitted/{origin_key}"))
    return str(uuid.uuid4())


def naturalize_bundle(bundle: dict[str, Any], *,
                      source_base: str | None = None) -> list[dict[str, Any]]:
    """Return the supported resources of `bundle`, naturalized into local
    identity with origin back-links. Does NOT mutate `bundle` (so the caller
    can still persist the as-submitted original as evidence).

    `source_base`: absolute FHIR base URL the bundle was pulled from, if known.
    When an entry carries an absolute `fullUrl`, that wins as `meta.source`
    (it is the literal source). Otherwise `source_base` + `Type/<foreign-id>`
    is used. With neither, the origin id is still preserved as an identifier
    but no resolvable `meta.source` can be recorded.
    """
    entries = bundle.get("entry") or []

    # ---- pass 1: decide every (foreign ref|fullUrl) -> local ref mapping ----
    id_map: dict[str, str] = {}
    plan: list[dict[str, Any]] = []
    for ent in entries:
        res = ent.get("resource")
        if not isinstance(res, dict):
            continue
        rt = res.get("resourceType")
        if rt not in SUPPORTED_TYPES:
            continue
        old_id = res.get("id")
        full_url = ent.get("fullUrl") if isinstance(ent.get("fullUrl"), str) else None
        origin_key = full_url or (f"{rt}/{old_id}" if old_id else None)
        new_id = _local_id(origin_key)
        new_ref = f"{rt}/{new_id}"
        if old_id:
            id_map[f"{rt}/{old_id}"] = new_ref
        if full_url:
            id_map[full_url] = new_ref
        plan.append({"res": res, "rt": rt, "old_id": old_id,
                     "full_url": full_url, "new_id": new_id})

    # ---- pass 2: emit naturalized copies with refs rewritten + back-links ----
    out: list[dict[str, Any]] = []
    for p in plan:
        res = copy.deepcopy(p["res"])
        res["id"] = p["new_id"]
        if p["old_id"]:
            add_source_identifier(res, p["old_id"])
        # meta.source: literal fullUrl wins, else construct from the source base
        if p["full_url"] and p["full_url"].startswith(("http://", "https://")):
            set_source(res, p["full_url"])
        elif source_base and p["old_id"]:
            set_source(res, f"{source_base.rstrip('/')}/{p['rt']}/{p['old_id']}")
        out.append(res)

    _rewrite_refs(out, id_map)
    _demote_dangling_urn_refs(out)
    return out
