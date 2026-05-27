"""bearer-token dependency + scope check."""
from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request

from app.auth.jwks import server_kid, server_public_key
from app.config import settings


class _Principal:
    __slots__ = ("client_id", "scopes")

    def __init__(self, client_id: str, scopes: list[str]) -> None:
        self.client_id = client_id
        self.scopes = scopes

    def has_scope(self, required: str) -> bool:
        if required in self.scopes:
            return True
        # wildcards: system/*.read covers any system/Foo.read
        if "/" in required and required.endswith(".read"):
            return "system/*.read" in self.scopes
        return False


def _extract_bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    return auth.split(" ", 1)[1].strip()


def verify_bearer(request: Request) -> _Principal:
    token = _extract_bearer(request)
    try:
        unverified_hdr = jwt.get_unverified_header(token)
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail=f"bad token header: {e}")
    if unverified_hdr.get("kid") != server_kid():
        raise HTTPException(status_code=401, detail="unknown signing key")
    try:
        payload = jwt.decode(
            token,
            key=server_public_key(),
            algorithms=["RS256"],
            audience=settings.base_url,
            issuer=settings.issuer,
        )
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail=f"bad token: {e}")
    scopes = payload.get("scope", "").split()
    return _Principal(client_id=payload.get("client_id", payload.get("sub", "")), scopes=scopes)


def require_scope(required: str):
    """factory for a Depends that asserts the principal has a specific scope."""

    def _check(principal: Annotated[_Principal, Depends(verify_bearer)]) -> _Principal:
        if not principal.has_scope(required):
            raise HTTPException(status_code=403, detail=f"missing scope: {required}")
        return principal

    return _check


Principal = _Principal  # public alias
