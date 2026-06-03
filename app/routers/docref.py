"""DocumentReference search + read (ITI-67).

each DocumentReference points (via content.attachment.url) at a Binary id of
the form 'doc-<patient>-<category>'. resolving the Binary triggers an
on-demand compile.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.auth.verify import Principal, require_scope
from app.fhir import store

router = APIRouter()


def _oo(severity: str, code: str, diag: str) -> dict:
    return {"resourceType": "OperationOutcome",
            "issue": [{"severity": severity, "code": code, "diagnostics": diag}]}


@router.get("/DocumentReference/{rid}", name="read_DocumentReference")
async def read_docref(
    rid: str,
    _p: Annotated[Principal, Depends(require_scope("system/DocumentReference.read"))],
) -> JSONResponse:
    res = store.read("DocumentReference", rid)
    if res is None:
        return JSONResponse(status_code=404, content=_oo("error", "not-found", f"DocumentReference/{rid}"))
    return JSONResponse(res, media_type="application/fhir+json")


@router.get("/DocumentReference", name="search_DocumentReference")
async def search_docref(
    request: Request,
    _p: Annotated[Principal, Depends(require_scope("system/DocumentReference.read"))],
) -> JSONResponse:
    norm: dict[str, list[str]] = {}
    for k, v in request.query_params.multi_items():
        norm.setdefault(k, []).append(v)

    results = list(store.list_all("DocumentReference"))

    if (v := norm.get("_id")):
        results = [r for r in results if r.get("id") == v[0]]
    # patient param supports (MHD-canonical + FHIR-canonical variants):
    #   ?patient=<uuid|slot|Patient/x>      direct reference
    #   ?patient.identifier=<system|value>  MHD ITI-67 chained search
    #   ?patient:identifier=<system|value>  FHIR ':identifier' modifier
    #   ?patient=<system>|<value>           identifier-token shorthand
    pat_ident = (norm.get("patient.identifier") or norm.get("patient:identifier") or [None])[0]
    pat_direct = (norm.get("patient") or [None])[0]
    if pat_ident is None and pat_direct and "|" in pat_direct:
        pat_ident, pat_direct = pat_direct, None
    if pat_ident:
        matches = store.find_patient_ids_by_identifier(pat_ident)
        if not matches:
            results = []
        else:
            results = [r for r in results
                       if any((r.get("subject", {}).get("reference", "")).endswith(f"Patient/{m}")
                              for m in matches)]
    elif pat_direct:
        canonical = store.resolve_patient_ref(pat_direct)
        if canonical is None:
            results = []
        else:
            results = [r for r in results
                       if (r.get("subject", {}).get("reference", "")).endswith(f"Patient/{canonical}")]
    # identifier token search (`system|value` preferred) — keeps DocumentReference
    # in sync with the generic store.search, so an origin/source id is findable here too.
    if (v := norm.get("identifier")):
        ident = v[0]
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
    if (v := norm.get("status")):
        results = [r for r in results if r.get("status") == v[0]]
    if (v := norm.get("category")):
        wanted = v[0].split("|")[-1]
        def cat_match(r):
            for c in (r.get("category") or []):
                for code in (c.get("coding") or []):
                    if code.get("code") == wanted:
                        return True
            return False
        results = [r for r in results if cat_match(r)]
    if (v := norm.get("type")):
        wanted = v[0].split("|")[-1]
        def type_match(r):
            for code in (r.get("type", {}).get("coding") or []):
                if code.get("code") == wanted:
                    return True
            return False
        results = [r for r in results if type_match(r)]

    # ---- DocumentReference-local metadata params (no chaining) ----
    # `date` (DocumentReference.date — metadata indexing time) vs `creation`
    # (content.attachment.creation — clinical document creation time). MHD/
    # ITI-67 (FHIR-56851) deliberately keeps these distinct, so we honour both.
    if (v := norm.get("date")):
        results = [r for r in results if store.match_date(v[0], r.get("date"))]
    if (v := norm.get("creation")):
        def creation_match(r):
            return any(store.match_date(v[0], (c.get("attachment") or {}).get("creation"))
                       for c in (r.get("content") or []))
        results = [r for r in results if creation_match(r)]
    if (v := norm.get("_lastupdated")):
        results = [r for r in results if store.match_date(v[0], (r.get("meta") or {}).get("lastUpdated"))]
    if (v := norm.get("format")):
        wanted = v[0].split("|")[-1]
        def format_match(r):
            return any(isinstance(c.get("format"), dict) and c["format"].get("code") == wanted
                       for c in (r.get("content") or []))
        results = [r for r in results if format_match(r)]
    if (v := norm.get("security-label")):
        results = [r for r in results if store.token_in(r.get("securityLabel"), v[0])]
    if (v := norm.get("related")):
        wanted = v[0]
        want_val = wanted.split("|")[-1]
        def related_match(r):
            for rel in ((r.get("context") or {}).get("related") or []):
                if isinstance(rel.get("reference"), str) and rel["reference"].endswith(want_val):
                    return True
                if (rel.get("identifier") or {}).get("value") == want_val:
                    return True
            return False
        results = [r for r in results if related_match(r)]

    # ---- chained params: resolved through the document's reference graph ----
    # These XDS-era parameters describe the clinical *context* of the document,
    # which on this server lives on the real Encounter/Practitioner resources
    # the document Bundle was broken open into — NOT denormalised onto the
    # DocumentReference. We chain to them at query time. The mapping
    # (setting->Encounter.serviceType, facility->Encounter.class,
    # event->Encounter.type, author.{given,family}->Practitioner.name) is
    # documented in docs/document-search-chaining.md.
    def _encounters_of(r):
        ctx_enc = (r.get("context") or {}).get("encounter")
        refs = ctx_enc if isinstance(ctx_enc, list) else ([ctx_enc] if ctx_enc else [])
        out = []
        for er in refs:
            tgt = store.resolve_reference((er or {}).get("reference"))
            if tgt is not None:
                out.append(tgt)
        return out

    if (v := norm.get("setting")):
        results = [r for r in results
                   if any(store.token_in(enc.get("serviceType"), v[0]) for enc in _encounters_of(r))]
    if (v := norm.get("facility")):
        results = [r for r in results
                   if any(store.token_in(enc.get("class"), v[0]) for enc in _encounters_of(r))]
    if (v := norm.get("event")):
        results = [r for r in results
                   if any(store.token_in(enc.get("type"), v[0]) for enc in _encounters_of(r))]
    if (v := norm.get("period")):
        def period_match(r):
            for enc in _encounters_of(r):
                per = enc.get("period") or {}
                if store.match_date(v[0], per.get("start")) or store.match_date(v[0], per.get("end")):
                    return True
            return False
        results = [r for r in results if period_match(r)]

    for chain_param, name_field in (("author.family", "family"), ("author.given", "given")):
        if (v := norm.get(chain_param)):
            wanted = v[0].lower()
            def author_match(r, field=name_field, want=wanted):
                for a in (r.get("author") or []):
                    tgt = store.resolve_reference(a.get("reference"))
                    if tgt is None:
                        continue
                    for nm in (tgt.get("name") or []):
                        vals = nm.get(field)
                        vals = [vals] if isinstance(vals, str) else (vals or [])
                        if any(isinstance(x, str) and x.lower().startswith(want) for x in vals):
                            return True
                return False
            results = [r for r in results if author_match(r)]

    return JSONResponse(store.bundle_searchset("DocumentReference", results, self_link=str(request.url)),
                        media_type="application/fhir+json")
