"""ITI-105 Simplified Publish: ``POST /DocumentReference``.

The body is a DocumentReference whose ``content[0].attachment.data`` holds the
document (a FHIR document Bundle) as base64. We:

  1. decode the bytes and compute ``size`` and ``hash`` (SHA-1, base64). If the
     client sent either, it must match.
  2. parse the Bundle (``application/fhir+json``, ``Bundle.type=document``) and
     check its structure.
  3. resolve the subject Patient: an existing Patient on this server (by the
     DocumentReference subject, or by a Patient.identifier in the Bundle), else
     the Patient inside the Bundle, created here.
  4. naturalize the Bundle's resources into local ids (``app/fhir/naturalize``)
     and store them, so they are searchable one by one too.
  5. store the exact bytes at ``data/documents/<id>.bin``. ``GET /Bundle/<id>``
     returns those bytes unchanged.
  6. store the DocumentReference with ``attachment.url = Bundle/<id>`` (no
     ``data``), and return 201 with a Location header.

The DocumentReference id, the Bundle URL id and the inbox submission id are the
same uuid, so one id follows a publish everywhere (UI, audit, validation).
"""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.auth.verify import Principal, require_scope
from app.config import settings
from app.fhir import store
from app.fhir.naturalize import SOURCE_ID_SYSTEM, _rewrite_refs, add_source_identifier, naturalize_bundle
from app.fhir.origin import COMMUNITY, tag_origin
from app.fhir.validate import structural_validate
from app.fhir.validation_queue import enqueue_submission

router = APIRouter()

PUBLISH_SCOPE = "system/DocumentReference.c"
FHIR_JSON = "application/fhir+json"


def _oo(code: str, diag: str, status: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status, media_type="application/fhir+json", content={
        "resourceType": "OperationOutcome",
        "issue": [{"severity": "error", "code": code, "diagnostics": diag}]})


def documents_dir() -> Path:
    return settings.data_dir / "documents"


def document_path(doc_id: str) -> Path | None:
    """Path of the stored bytes for ``doc_id``, or None if there are none."""
    if not doc_id or "/" in doc_id or ".." in doc_id:
        return None
    p = documents_dir() / f"{doc_id}.bin"
    return p if p.is_file() else None


def sha1_b64(data: bytes) -> str:
    return base64.b64encode(hashlib.sha1(data).digest()).decode("ascii")  # noqa: S324 — FHIR Attachment.hash is SHA-1


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _existing_patient(docref: dict[str, Any], bundle_patient: dict[str, Any] | None) -> str | None:
    """An existing local Patient this publish is about, or None."""
    subj = docref.get("subject") or {}
    ref = subj.get("reference")
    if isinstance(ref, str) and ref.startswith("Patient/"):
        pid = ref.split("/", 1)[1]
        if store.read("Patient", pid) is not None:
            return pid
    ident = subj.get("identifier") or {}
    if ident.get("system") and ident.get("value"):
        hits = store.find_patient_ids_by_identifier(f"{ident['system']}|{ident['value']}")
        if len(hits) == 1:
            return next(iter(hits))
    for i in (bundle_patient or {}).get("identifier") or []:
        if not i.get("system") or not i.get("value") or i["system"] == SOURCE_ID_SYSTEM:
            continue
        hits = store.find_patient_ids_by_identifier(f"{i['system']}|{i['value']}")
        if len(hits) == 1:
            return next(iter(hits))
    return None


def _bundle_subject(bundle: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """(reference the Composition uses for its subject, that Patient resource)."""
    entries = bundle.get("entry") or []
    comp = next((e.get("resource") for e in entries
                 if (e.get("resource") or {}).get("resourceType") == "Composition"), None)
    want = ((comp or {}).get("subject") or {}).get("reference")
    for e in entries:
        res = e.get("resource") or {}
        if res.get("resourceType") != "Patient":
            continue
        keys = {e.get("fullUrl"), f"Patient/{res.get('id')}"}
        if want is None or want in keys or (isinstance(want, str) and want.endswith(f"Patient/{res.get('id')}")):
            return want, res
    # the Composition's subject reference does not resolve inside the Bundle;
    # if the Bundle holds exactly one Patient, that is who it is about
    pats = [e.get("resource") for e in entries if (e.get("resource") or {}).get("resourceType") == "Patient"]
    return want, (pats[0] if len(pats) == 1 else None)


@router.post("/DocumentReference", name="iti_105_simplified_publish")
async def publish_document(
    request: Request,
    principal: Annotated[Principal, Depends(require_scope(PUBLISH_SCOPE))],
) -> JSONResponse:
    try:
        docref = json.loads(await request.body())
    except (ValueError, UnicodeDecodeError):
        return _oo("structure", "body is not JSON")
    return publish(docref)


def publish(docref: Any) -> JSONResponse:
    """ITI-105 Simplified Publish of one DocumentReference (data inline).
    Shared by the HTTP route and scripts/republish_inbox.py."""
    if not isinstance(docref, dict) or docref.get("resourceType") != "DocumentReference":
        return _oo("invalid", "expected a DocumentReference")
    content = docref.get("content")
    if not isinstance(content, list) or not content or not isinstance(content[0], dict):
        return _oo("required", "DocumentReference.content[0].attachment is required")
    att = content[0].get("attachment") or {}
    if not isinstance(att.get("data"), str) or not att["data"]:
        return _oo("required", "content[0].attachment.data must hold the document as base64 (ITI-105 Simplified Publish)")

    # ---- 1. bytes, size, hash ----
    try:
        raw = base64.b64decode(att["data"], validate=True)
    except (binascii.Error, ValueError):
        return _oo("value", "content[0].attachment.data is not valid base64")
    size, digest = len(raw), sha1_b64(raw)
    if att.get("size") is not None and att["size"] != size:
        return _oo("value", f"attachment.size is {att['size']} but the decoded document is {size} bytes")
    if att.get("hash") and att["hash"] != digest:
        return _oo("value", f"attachment.hash does not match the SHA-1 of the document ({digest})")

    # ---- 2. the document Bundle ----
    content_type = att.get("contentType") or FHIR_JSON
    if content_type.split(";")[0].strip() not in (FHIR_JSON, "application/json"):
        return _publish_binary(docref, att, content_type, raw, size, digest)
    try:
        bundle = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return _oo("structure", "the decoded document is not JSON")
    if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle" or bundle.get("type") != "document":
        return _oo("invalid", "the decoded document must be a Bundle with type=document")
    ok, problems = structural_validate(bundle)
    if not ok:
        return JSONResponse(status_code=400, media_type="application/fhir+json", content={
            "resourceType": "OperationOutcome",
            "issue": [{"severity": "error", "code": "structure", "diagnostics": p} for p in problems]})

    # ---- 3. + 4. patient and resources ----
    doc_id = str(uuid.uuid4())
    subject_ref, bundle_patient = _bundle_subject(bundle)
    existing = _existing_patient(docref, bundle_patient)
    resources = naturalize_bundle(bundle, submission_id=doc_id)
    new_pat_ref = None
    if bundle_patient is not None:
        # the naturalized copy of the bundle's Patient
        src = f"Patient/{bundle_patient.get('id')}"
        for r in resources:
            if r["resourceType"] == "Patient" and any(
                    i.get("system") == SOURCE_ID_SYSTEM and f"Patient/{i.get('value')}" == src
                    for i in r.get("identifier") or []):
                new_pat_ref = f"Patient/{r['id']}"
                break
        if new_pat_ref is None:
            pats = [r for r in resources if r["resourceType"] == "Patient"]
            new_pat_ref = f"Patient/{pats[0]['id']}" if pats else None
    if existing:
        patient_id = existing
        if new_pat_ref:
            _rewrite_refs(resources, {new_pat_ref: f"Patient/{existing}"})
            resources = [r for r in resources if f"{r['resourceType']}/{r['id']}" != new_pat_ref]
    elif new_pat_ref:
        patient_id = new_pat_ref.split("/", 1)[1]
    else:
        return _oo("not-found", "could not resolve the subject: the DocumentReference.subject is not a Patient on "
                   "this server and the Bundle carries no Patient", status=422)

    # ---- 5. the DocumentReference (built in full before anything is written) ----
    out = copy.deepcopy(docref)
    if docref.get("id"):
        add_source_identifier(out, str(docref["id"]))
    out["id"] = doc_id
    out["status"] = out.get("status") or "current"
    out["subject"] = {"reference": f"Patient/{patient_id}"}
    out.setdefault("date", _now())
    comp = next((e.get("resource") for e in bundle.get("entry") or []
                 if (e.get("resource") or {}).get("resourceType") == "Composition"), {})
    if not out.get("type") and comp.get("type"):
        out["type"] = comp["type"]
    if not out.get("masterIdentifier") and isinstance(bundle.get("identifier"), dict):
        out["masterIdentifier"] = bundle["identifier"]
    new_att = {k: v for k, v in att.items() if k != "data"}
    new_att.update({"contentType": content_type, "url": f"Bundle/{doc_id}", "size": size, "hash": digest})
    new_att.setdefault("creation", bundle.get("timestamp") or comp.get("date"))
    if not new_att.get("creation"):
        new_att.pop("creation")
    out["content"] = [dict(content[0], attachment=new_att)] + [
        c for c in content[1:] if not (c.get("attachment") or {}).get("data")]
    # references to resources inside the bundle (author, encounter...) follow
    # the bundle's naturalized ids; anything still unresolvable is dropped
    # rather than left dangling.
    id_map = {}
    for r in resources:
        for i in _identifiers(r):
            if i.get("system") == SOURCE_ID_SYSTEM:
                id_map[f"{r['resourceType']}/{i['value']}"] = f"{r['resourceType']}/{r['id']}"
    for e in bundle.get("entry") or []:
        res = e.get("resource") or {}
        if e.get("fullUrl") and f"{res.get('resourceType')}/{res.get('id')}" in id_map:
            id_map[e["fullUrl"]] = id_map[f"{res['resourceType']}/{res['id']}"]
    if new_pat_ref:
        for k, v in list(id_map.items()):
            if v == new_pat_ref:
                id_map[k] = f"Patient/{patient_id}"
    if subject_ref:
        id_map.setdefault(subject_ref, f"Patient/{patient_id}")
    _rewrite_refs({k: v for k, v in out.items() if k != "subject"}, id_map)
    _drop_dangling(out)
    meta = out.setdefault("meta", {})
    meta.pop("versionId", None)
    meta["lastUpdated"] = _now()
    tag_origin(out, COMMUNITY, submission_id=doc_id)

    # ---- 6. write: evidence, exact bytes, resources, then the DocumentReference ----
    # Nothing above writes, so a failure there leaves the store untouched; the
    # DocumentReference goes last so it never points at content not yet stored.
    inbox = settings.data_dir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / f"{doc_id}.json").write_text(json.dumps(bundle, indent=2, sort_keys=True))
    documents_dir().mkdir(parents=True, exist_ok=True)
    (documents_dir() / f"{doc_id}.bin").write_bytes(raw)
    for r in resources:
        store.write(r)
    store.write(out)

    # EU-profile validation is a badge, never a gate
    enqueue_submission(doc_id)

    return JSONResponse(status_code=201, content=out, media_type="application/fhir+json", headers={
        "Location": f"{settings.base_url}/DocumentReference/{doc_id}",
        "ETag": 'W/"1"',
    })


def _identifiers(res: dict[str, Any]) -> list[dict[str, Any]]:
    """A resource's identifiers as a list. R4 has both shapes: Patient.identifier
    is 0..*, but Composition.identifier and Bundle.identifier are 0..1 (one object)."""
    ident = res.get("identifier")
    if isinstance(ident, dict):
        return [ident]
    return [i for i in ident or [] if isinstance(i, dict)]


def _publish_binary(docref: dict[str, Any], att: dict[str, Any], content_type: str,
                    raw: bytes, size: int, digest: str) -> JSONResponse:
    """Non-FHIR content (PDF, text...): stored as a Binary and served at
    Binary/<id>. With no Bundle to take a Patient from, the subject must be a
    Patient this server already holds."""
    patient_id = _existing_patient(docref, None)
    if patient_id is None:
        return _oo("not-found", "for non-FHIR content the subject must be a Patient on this server "
                   "(by reference or identifier); publish a FHIR document to bring a new Patient", status=422)
    doc_id = str(uuid.uuid4())
    binary = {"resourceType": "Binary", "id": doc_id, "contentType": content_type,
              "data": base64.b64encode(raw).decode("ascii"), "meta": {"lastUpdated": _now()}}
    tag_origin(binary, COMMUNITY, submission_id=doc_id)
    out = copy.deepcopy(docref)
    if docref.get("id"):
        add_source_identifier(out, str(docref["id"]))
    out["id"] = doc_id
    out["status"] = out.get("status") or "current"
    out["subject"] = {"reference": f"Patient/{patient_id}"}
    out.setdefault("date", _now())
    new_att = {k: v for k, v in att.items() if k != "data"}
    new_att.update({"contentType": content_type, "url": f"Binary/{doc_id}", "size": size, "hash": digest})
    out["content"] = [dict(docref["content"][0], attachment=new_att)]
    _drop_dangling(out)
    meta = out.setdefault("meta", {})
    meta.pop("versionId", None)
    meta["lastUpdated"] = _now()
    tag_origin(out, COMMUNITY, submission_id=doc_id)
    # write only once both are built; the Binary first, so the reference resolves
    store.write(binary)
    store.write(out)
    return JSONResponse(status_code=201, content=out, media_type="application/fhir+json", headers={
        "Location": f"{settings.base_url}/DocumentReference/{doc_id}", "ETag": 'W/"1"'})


def _drop_dangling(docref: dict[str, Any]) -> None:
    """Remove literal references (outside subject) that do not resolve here."""
    def ok(ref: dict) -> bool:
        r = ref.get("reference")
        return not isinstance(r, str) or r.startswith("#") or store.resolve_reference(r) is not None

    for key in ("author", "custodian", "authenticator"):
        val = docref.get(key)
        if isinstance(val, list):
            kept = [r for r in val if not isinstance(r, dict) or ok(r) or r.get("display")]
            for r in kept:
                if isinstance(r, dict) and not ok(r):
                    r.pop("reference")
            if kept:
                docref[key] = kept
            else:
                docref.pop(key)
        elif isinstance(val, dict) and not ok(val):
            val.pop("reference")
            if not val:
                docref.pop(key)
    ctx = docref.get("context")
    if isinstance(ctx, dict):
        for key in ("encounter", "related"):
            if isinstance(ctx.get(key), list):
                ctx[key] = [r for r in ctx[key] if not isinstance(r, dict) or ok(r)]
                if not ctx[key]:
                    ctx.pop(key)
        sp = ctx.get("sourcePatientInfo")
        if isinstance(sp, dict) and not ok(sp):
            ctx.pop("sourcePatientInfo")
