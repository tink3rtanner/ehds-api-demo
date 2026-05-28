"""Patient router with PDQm-style search + $match + $everything.

PDQm search params: identifier, family, given, name, birthdate, gender,
address-*, telecom, phone, email. all case-insensitive substring on string
fields, exact on identifier value, exact on birthdate yyyy-mm-dd.
"""
from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse

from app.auth.verify import Principal, require_scope
from app.fhir import store

router = APIRouter()


def _oo(severity: str, code: str, diagnostics: str) -> dict:
    return {"resourceType": "OperationOutcome", "issue": [{"severity": severity, "code": code, "diagnostics": diagnostics}]}


def _iget(d: dict, *path: str, default: Any = None) -> Any:
    cur: Any = d
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
    return cur if cur is not None else default


def _gather_names(p: dict) -> list[tuple[list[str], list[str]]]:
    """return list of (given_parts, family_parts) for each HumanName."""
    out = []
    for n in p.get("name", []) or []:
        given = n.get("given") or []
        family = [n["family"]] if n.get("family") else []
        out.append(([g.lower() for g in given], [f.lower() for f in family]))
    return out


def _gather_addresses(p: dict) -> list[dict]:
    return p.get("address", []) or []


def _gather_telecoms(p: dict) -> list[dict]:
    return p.get("telecom", []) or []


def _match_string_field(values: list[str], q: str) -> bool:
    needle = q.lower()
    return any(needle in v for v in values)


def _pdqm_search(params: dict[str, list[str]]) -> list[dict]:
    patients = list(store.list_all("Patient"))

    def keep(p: dict) -> bool:
        # _id
        if (v := params.get("_id")) and p.get("id") != v[0]:
            return False
        # identifier
        if (v := params.get("identifier")):
            wanted = v[0].split("|")[-1]
            if not any((i.get("value") == wanted) for i in p.get("identifier", []) or []):
                return False
        # name parts
        names = _gather_names(p)
        if (v := params.get("family")):
            if not any(_match_string_field(fam, v[0]) for _, fam in names):
                return False
        if (v := params.get("given")):
            if not any(_match_string_field(giv, v[0]) for giv, _ in names):
                return False
        if (v := params.get("name")):
            all_parts = [n for giv, fam in names for n in giv + fam]
            if not _match_string_field(all_parts, v[0]):
                return False
        # birthdate (eq only)
        if (v := params.get("birthdate")):
            if (p.get("birthDate") or "") != v[0]:
                return False
        # gender (exact)
        if (v := params.get("gender")) and p.get("gender") != v[0]:
            return False
        # address-* and address (free-form)
        for k in ("address", "address-city", "address-state", "address-postalcode", "address-country"):
            if (v := params.get(k)):
                field = k.split("-", 1)[1] if "-" in k else None
                hit = False
                for a in _gather_addresses(p):
                    if field is None:
                        # match against any of city/state/postalCode/country/line/text
                        blob = " ".join([
                            a.get("city", ""), a.get("state", ""), a.get("postalCode", ""),
                            a.get("country", ""), a.get("text", ""),
                            *(a.get("line") or []),
                        ]).lower()
                        if v[0].lower() in blob:
                            hit = True
                            break
                    else:
                        fhir_field = {"postalcode": "postalCode"}.get(field, field)
                        if v[0].lower() in (a.get(fhir_field, "") or "").lower():
                            hit = True
                            break
                if not hit:
                    return False
        # telecom / phone / email
        for k, system_filter in (("telecom", None), ("phone", "phone"), ("email", "email")):
            if (v := params.get(k)):
                hit = False
                for t in _gather_telecoms(p):
                    if system_filter and t.get("system") != system_filter:
                        continue
                    if v[0].lower() in (t.get("value", "") or "").lower():
                        hit = True
                        break
                if not hit:
                    return False
        return True

    return [p for p in patients if keep(p)]


# --------- weighted $match ---------

def _name_pairs(p: dict) -> list[tuple[str, str]]:
    pairs = []
    for n in p.get("name", []) or []:
        family = (n.get("family") or "").lower()
        given_list = n.get("given") or []
        given = (given_list[0] if given_list else "").lower()
        pairs.append((family, given))
    return pairs


def _score(candidate: dict, q: dict) -> float:
    """rough Fellegi-Sunter-ish weighted score; bounded [0,1]."""
    score = 0.0
    weight = 0.0
    # identifier hits dominate — exact match short-circuits to certain
    q_ids = {i.get("value") for i in (q.get("identifier") or []) if i.get("value")}
    if q_ids:
        if q_ids & {i.get("value") for i in (candidate.get("identifier") or [])}:
            return 1.0
        # identifier was provided but didn't hit — heavy negative weight
        weight += 0.6
    q_names = _name_pairs(q)
    c_names = _name_pairs(candidate)
    if q_names and c_names:
        weight += 0.3
        best = 0.0
        for qf, qg in q_names:
            for cf, cg in c_names:
                s = 0.0
                if qf and cf and qf == cf:
                    s += 0.18
                elif qf and cf and (qf in cf or cf in qf):
                    s += 0.08
                if qg and cg and qg == cg:
                    s += 0.12
                elif qg and cg and (qg in cg or cg in qg):
                    s += 0.05
                if s > best:
                    best = s
        score += best
    qbd = q.get("birthDate")
    if qbd:
        weight += 0.2
        if qbd == candidate.get("birthDate"):
            score += 0.2
        elif qbd[:4] == (candidate.get("birthDate") or "")[:4]:
            score += 0.06
    qgender = q.get("gender")
    if qgender:
        weight += 0.05
        if qgender == candidate.get("gender"):
            score += 0.05
    if weight == 0:
        return 0.0
    return min(score / weight, 1.0)


def _grade(score: float) -> str:
    if score >= 0.9:
        return "certain"
    if score >= 0.7:
        return "probable"
    if score >= 0.4:
        return "possible"
    return "certainly-not"


# --------- routes ---------

@router.get("/Patient/{rid}", name="read_Patient")
async def read_patient(
    rid: str,
    _p: Annotated[Principal, Depends(require_scope("system/Patient.read"))],
) -> JSONResponse:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", rid):
        return JSONResponse(status_code=400, content=_oo("error", "invalid", "bad id"))
    res = store.read("Patient", rid)
    if res is None:
        return JSONResponse(status_code=404, content=_oo("error", "not-found", f"Patient/{rid}"))
    return JSONResponse(res, media_type="application/fhir+json")


@router.get("/Patient/{rid}/$summary", name="patient_summary")
async def patient_summary(
    rid: str,
    _p: Annotated[Principal, Depends(require_scope("system/Patient.read"))],
) -> JSONResponse:
    """IPS Patient Summary operation.

    https://hl7.org/fhir/uv/ips/OperationDefinition-summary.html

    Returns a FHIR Bundle.type=document containing the patient summary
    (Composition + Patient + clinical resources). Identical content to
    /Bundle/{deterministic-uuid} for category=patient-summary; this
    operation gives clients the canonical IPS URL they expect.
    """
    from app.fhir import document as _dc
    if store.read("Patient", rid) is None:
        return JSONResponse(status_code=404, content=_oo("error", "not-found", f"Patient/{rid}"))
    try:
        bundle = _dc.compile_document(rid, "patient-summary")
    except _dc.MissingResources as e:
        return JSONResponse(status_code=422, content=_oo("error", "not-supported", str(e)))
    return JSONResponse(bundle, media_type="application/fhir+json")


@router.get("/Patient", name="search_Patient")
async def search_patients(
    request: Request,
    _p: Annotated[Principal, Depends(require_scope("system/Patient.read"))],
) -> JSONResponse:
    norm: dict[str, list[str]] = {}
    for k, v in request.query_params.multi_items():
        norm.setdefault(k, []).append(v)
    results = _pdqm_search(norm)
    return JSONResponse(store.bundle_searchset("Patient", results), media_type="application/fhir+json")


@router.post("/Patient/$match", name="patient_match")
async def patient_match(
    _p: Annotated[Principal, Depends(require_scope("system/Patient.read"))],
    body: dict = Body(...),
) -> JSONResponse:
    if body.get("resourceType") != "Parameters":
        return JSONResponse(status_code=400, content=_oo("error", "invalid", "expected Parameters resource"))
    resource_param = next((p for p in body.get("parameter", []) if p.get("name") == "resource"), None)
    if not resource_param or "resource" not in resource_param:
        return JSONResponse(status_code=400, content=_oo("error", "invalid", "missing 'resource' parameter"))
    query = resource_param["resource"]
    if query.get("resourceType") != "Patient":
        return JSONResponse(status_code=400, content=_oo("error", "invalid", "resource must be Patient"))
    count = 5
    for p in body.get("parameter", []):
        if p.get("name") == "count" and isinstance(p.get("valueInteger"), int):
            count = p["valueInteger"]

    scored: list[tuple[float, dict]] = []
    for cand in store.list_all("Patient"):
        s = _score(cand, query)
        if s > 0:
            scored.append((s, cand))
    scored.sort(key=lambda x: -x[0])
    top = scored[:count]

    entries = []
    for s, cand in top:
        entries.append({
            "fullUrl": f"{request_base_url()}/Patient/{cand['id']}",
            "resource": cand,
            "search": {
                "extension": [{
                    "url": "http://hl7.org/fhir/StructureDefinition/match-grade",
                    "valueCode": _grade(s),
                }],
                "mode": "match",
                "score": round(s, 4),
            },
        })
    return JSONResponse({
        "resourceType": "Bundle",
        "type": "searchset",
        "total": len(entries),
        "entry": entries,
    }, media_type="application/fhir+json")


def request_base_url() -> str:
    from app.config import settings
    return settings.base_url
