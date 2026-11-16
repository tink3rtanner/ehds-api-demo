"""SMART scope matching, v1 and v2 syntax.

A scope is ``system/<Type|*>.<perms>``. The perms part is either the v1 form
(``read``, ``write``, ``*``) or the v2 form (any ordered subset of ``cruds``).

    read  = rs        write = cud        *  = cruds

``covers(granted, required)`` answers "does holding ``granted`` let you do
``required``?". So ``system/*.write`` covers ``system/DocumentReference.c``,
and ``system/*.rs`` covers ``system/Patient.read``.
"""
from __future__ import annotations

import re

_SCOPE_RE = re.compile(r"^system/(?P<type>\*|[A-Z][A-Za-z]+)\.(?P<perms>read|write|\*|c?r?u?d?s?)$")
_V1 = {"read": "rs", "write": "cud", "*": "cruds"}


def parse(scope: str) -> tuple[str, frozenset[str]] | None:
    """``(type, set of cruds letters)`` or None when the scope is not a valid system scope."""
    m = _SCOPE_RE.match(scope or "")
    if not m or not m.group("perms"):
        return None
    perms = m.group("perms")
    return m.group("type"), frozenset(_V1.get(perms, perms))


def is_valid(scope: str) -> bool:
    return parse(scope) is not None


def covers(granted: str, required: str) -> bool:
    g, r = parse(granted), parse(required)
    if g is None or r is None:
        return granted == required
    gtype, gperms = g
    rtype, rperms = r
    if gtype not in ("*", rtype):
        return False
    return rperms <= gperms


def any_covers(granted: list[str] | tuple[str, ...], required: str) -> bool:
    return any(covers(g, required) for g in granted)
