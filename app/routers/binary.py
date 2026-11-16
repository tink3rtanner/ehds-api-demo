"""GET /Binary/{id} — legacy alias.

Compiled FHIR documents now live at /Bundle/{uuid}. This router preserves
backward compatibility for two URL shapes:

  - /Binary/{bundle-uuid}  -> 301 to /Bundle/{bundle-uuid}
  - /Binary/doc-{patient}-{category}  (the old hand-rolled id scheme)
        -> resolved to the deterministic uuid and 301 to /Bundle/{uuid}

Stored Binaries (attachments uploaded in a Bundle, and non-FHIR content
published with ITI-105) are served for real; see read_binary.
"""
from __future__ import annotations

import base64
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from app.auth.verify import verify_scope
from app.fhir import document as doc_compile
from app.fhir import store
from app.fhir.ids import bundle_id
from app.fhir.naturalize import SOURCE_ID_SYSTEM

router = APIRouter()

# build regex once from the source-of-truth category list
_LEGACY_DOC_ID_RE = re.compile(
    r"^doc-(?P<patient>[A-Za-z0-9._]+(?:-[A-Za-z0-9._]+)*?)"
    r"-(?P<category>" + "|".join(re.escape(k) for k in doc_compile.CATEGORY_TO_DOC_TYPE.keys()) + ")$"
)


def find_binary(rid: str) -> dict | None:
    """A stored Binary by local id, else by the id its submitter gave it."""
    res = store.read("Binary", rid)
    if res is not None:
        return res
    hits = [b for b in store.list_all("Binary")
            if any(t.get("system") == SOURCE_ID_SYSTEM and t.get("code") == rid
                   for t in (b.get("meta") or {}).get("tag") or [])]
    hits.sort(key=lambda b: (b.get("meta") or {}).get("lastUpdated") or "", reverse=True)
    return hits[0] if hits else None


@router.get("/Binary/{rid}", name="read_Binary")
async def read_binary(request: Request, rid: str):
    """ITI-68 for non-FHIR content. A stored Binary comes back as the FHIR
    Binary resource when the client asks for JSON, else as its raw bytes with
    its own contentType. Anything else is a legacy alias for a document Bundle."""
    if "/" in rid or ".." in rid:
        raise HTTPException(status_code=400, detail="bad id")
    if store.read("Binary", rid) is not None or find_binary(rid) is not None:
        verify_scope(request, "system/Binary.read")
        res = find_binary(rid)
        accept = request.headers.get("accept", "")
        if "json" in accept:
            return JSONResponse(res, media_type="application/fhir+json")
        return Response(content=base64.b64decode(res.get("data") or ""),
                        media_type=res.get("contentType") or "application/octet-stream")
    m = _LEGACY_DOC_ID_RE.match(rid)
    target_id = bundle_id(m.group("patient"), m.group("category")) if m else rid
    return RedirectResponse(url=f"/Bundle/{target_id}", status_code=301)
