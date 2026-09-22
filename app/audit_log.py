"""Read side of the request audit log (written by app.security.StructuredLogMiddleware).

One JSON line per request lives in ``data/audit/audit-YYYY-MM-DD.jsonl``. This
module answers the UI's Activity page: a filtered tail and aggregate stats.

Scope
-----
An internet-facing box gets a steady stream of scanners probing for
``/wp-config.php`` and ``/.env``. Those are real requests and stay in the file,
but they are noise for both uses of the page (debugging an integration, and
the connectathon "receipt" of what a client just did). Every entry is
classified as one of:

* ``fhir``  — the API surface: resources, token, metadata, discovery, publish
* ``ui``    — the viewer's own traffic under ``/ui``
* ``noise`` — everything else

and queries default to ``scope=fhir``.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from app.config import settings
from app.fhir.store import SUPPORTED_TYPES

_FHIR_PREFIXES = tuple(f"/{t}" for t in SUPPORTED_TYPES) + (
    "/Bundle", "/Binary", "/token", "/metadata", "/.well-known/", "/register-client",
    "/spec/", "/Epic/", "/healthz", "/llms.txt",
)
_TYPE_PATH_RE = re.compile(r"^/([A-Z][A-Za-z]+)(/|$|\?)")
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def classify(path: str) -> str:
    if path == "/":
        return "fhir"
    if path == "/ui" or path.startswith("/ui/"):
        return "ui"
    for pfx in _FHIR_PREFIXES:
        if path == pfx or path.startswith(pfx + "/") or path.startswith(pfx + "?") or (
                pfx.endswith("/") and path.startswith(pfx)):
            return "fhir"
    return "noise"


def collapse_path(path: str) -> str:
    """``/Patient/<uuid>`` -> ``/Patient/{id}`` so stats group by route."""
    m = _TYPE_PATH_RE.match(path)
    if m and path.count("/") >= 2:
        rest = path[len(m.group(1)) + 1:]
        parts = rest.split("/")
        if len(parts) >= 2 and parts[1] and not parts[1].startswith("$"):
            tail = "/" + "/".join(parts[2:]) if len(parts) > 2 else ""
            return f"/{m.group(1)}/{{id}}{tail}"
    return _UUID_RE.sub("{id}", path)


def _files(days: int) -> list[Path]:
    today = date.today()
    out = []
    for back in range(max(1, days)):
        f = settings.audit_log_dir / f"audit-{(today - timedelta(days=back)).isoformat()}.jsonl"
        if f.exists():
            out.append(f)
    return out


def _iter_entries(days: int, *, newest_first: bool):
    files = _files(days)
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        seq = reversed(lines) if newest_first else lines
        for line in seq:
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def query(*, limit: int = 100, days: int = 7, scope: str = "fhir", method: str | None = None,
          path_prefix: str | None = None, status_min: int = 100, status_max: int = 599,
          client_id: str | None = None, patient: str | None = None, run: str | None = None,
          since: str | None = None) -> dict[str, Any]:
    """Most recent ``limit`` entries matching every given filter (newest first).

    ``patient`` matches the id anywhere in the path or query string (so a
    compartment search and a direct read both count). ``run`` matches the
    ``X-Demo-Run`` header the scenario sends. ``since`` is an ISO timestamp
    lower bound.
    """
    limit = max(1, min(int(limit), 1000))
    out: list[dict[str, Any]] = []
    truncated = False
    for e in _iter_entries(days, newest_first=True):
        path = e.get("path") or ""
        if since and (e.get("ts") or "") < since:
            continue
        if scope != "all" and classify(path) != scope:
            continue
        if method and e.get("method") != method.upper():
            continue
        if path_prefix and not path.startswith(path_prefix):
            continue
        s = e.get("status", 0)
        if not (status_min <= s <= status_max):
            continue
        if client_id and e.get("client_id") != client_id:
            continue
        if run and e.get("run") != run:
            continue
        if patient and patient not in path and patient not in (e.get("query") or ""):
            continue
        e = dict(e)
        e["scope"] = classify(path)
        out.append(e)
        if len(out) >= limit:
            truncated = True
            break
    return {"total": len(out), "entries": out, "truncated": truncated, "scope": scope}


def stats(*, days: int = 1, scope: str = "fhir") -> dict[str, Any]:
    total = 0
    by_scope: Counter = Counter()
    by_status_class: Counter = Counter()
    by_method: Counter = Counter()
    by_path: Counter = Counter()
    by_client: Counter = Counter()
    latencies: list[int] = []
    for e in _iter_entries(days, newest_first=False):
        path = e.get("path") or ""
        sc = classify(path)
        by_scope[sc] += 1
        if scope != "all" and sc != scope:
            continue
        total += 1
        s = e.get("status", 0)
        by_status_class[f"{s // 100}xx"] += 1
        by_method[e.get("method", "?")] += 1
        by_path[collapse_path(path)] += 1
        if e.get("client_id"):
            by_client[e["client_id"]] += 1
        d = e.get("dur_ms")
        if isinstance(d, int):
            latencies.append(d)
    latencies.sort()

    def _pct(p: float) -> int | None:
        if not latencies:
            return None
        return latencies[min(int(len(latencies) * p / 100), len(latencies) - 1)]

    return {
        "total": total,
        "days": days,
        "scope": scope,
        "by_scope": dict(by_scope),
        "by_status_class": dict(by_status_class),
        "by_method": dict(by_method),
        "top_paths": by_path.most_common(12),
        "top_clients": by_client.most_common(10),
        "latency_ms": {"p50": _pct(50), "p95": _pct(95), "p99": _pct(99),
                       "max": latencies[-1] if latencies else None},
    }
