"""Epic on FHIR R4 client (Backend OAuth 2.0 / JWT bearer).

Single-process in-memory token cache. RS384-signed client_assertion per
Epic spec. Read-only — search + read only, no writes.

Config (env):
    EPIC_CLIENT_ID            (required)  non-production client_id from Epic
    EPIC_PRIVATE_KEY_PATH     (required)  PEM private key matching the public
                                          key Epic fetched from our JWKS URL
    EPIC_KID                  (required)  the kid we advertise in our JWKS;
                                          MUST match the JWT header's kid
    EPIC_TOKEN_URL            (default Epic sandbox token endpoint)
    EPIC_FHIR_BASE            (default Epic sandbox R4 base)
    EPIC_HTTP_TIMEOUT         (default 30s)
"""
from __future__ import annotations

import os
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import jwt  # PyJWT

SANDBOX_TOKEN_URL = "https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token"  # noqa: S105
SANDBOX_FHIR_BASE = "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4"


class EpicConfigError(RuntimeError):
    pass


class EpicAuthError(RuntimeError):
    pass


class EpicClient:
    def __init__(
        self,
        client_id: str | None = None,
        private_key_path: str | Path | None = None,
        kid: str | None = None,
        token_url: str | None = None,
        fhir_base: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.client_id = client_id or os.environ.get("EPIC_CLIENT_ID")
        self.private_key_path = Path(
            private_key_path or os.environ.get("EPIC_PRIVATE_KEY_PATH", "")
        )
        self.kid = kid or os.environ.get("EPIC_KID")
        self.token_url = (token_url or os.environ.get("EPIC_TOKEN_URL")
                          or SANDBOX_TOKEN_URL)
        self.fhir_base = (fhir_base or os.environ.get("EPIC_FHIR_BASE")
                          or SANDBOX_FHIR_BASE).rstrip("/")
        self.timeout = timeout or float(os.environ.get("EPIC_HTTP_TIMEOUT", "30"))

        missing = [n for n, v in [
            ("EPIC_CLIENT_ID", self.client_id),
            ("EPIC_PRIVATE_KEY_PATH", str(self.private_key_path) if self.private_key_path else None),
            ("EPIC_KID", self.kid),
        ] if not v]
        if missing:
            raise EpicConfigError(f"missing required env vars: {', '.join(missing)}")
        if not self.private_key_path.exists():
            raise EpicConfigError(f"private key not found: {self.private_key_path}")

        self._access_token: str | None = None
        self._expires_at: float = 0.0
        self._key_bytes = self.private_key_path.read_bytes()

    # ------------------- auth -------------------

    def _mint_assertion(self, lifetime: int = 240) -> str:
        now = int(time.time())
        claims = {
            "iss": self.client_id,
            "sub": self.client_id,
            "aud": self.token_url,
            "jti": str(uuid.uuid4()),
            "iat": now,
            "nbf": now,
            "exp": now + lifetime,
        }
        return jwt.encode(
            claims,
            self._key_bytes,
            algorithm="RS384",
            headers={"alg": "RS384", "typ": "JWT", "kid": self.kid},
        )

    def _refresh(self) -> None:
        with httpx.Client(timeout=self.timeout) as h:
            r = h.post(
                self.token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                    "client_assertion": self._mint_assertion(),
                },
                headers={"Accept": "application/json"},
            )
        if r.status_code >= 400:
            raise EpicAuthError(f"token {r.status_code}: {r.text}")
        body = r.json()
        self._access_token = body["access_token"]
        # Epic returns expires_in in seconds; refresh 30s early to avoid races.
        self._expires_at = time.time() + int(body.get("expires_in", 3600)) - 30

    def token(self) -> str:
        if not self._access_token or time.time() >= self._expires_at:
            self._refresh()
        assert self._access_token is not None
        return self._access_token

    # ------------------- HTTP -------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token()}",
            "Accept": "application/fhir+json",
        }

    def get(self, path_or_url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = path_or_url if path_or_url.startswith("http") else f"{self.fhir_base}{path_or_url}"
        with httpx.Client(timeout=self.timeout) as h:
            r = h.get(url, headers=self._headers(), params=params)
        if r.status_code == 401:
            self._access_token = None
            with httpx.Client(timeout=self.timeout) as h:
                r = h.get(url, headers=self._headers(), params=params)
        r.raise_for_status()
        return r.json()

    def read(self, rtype: str, rid: str) -> dict[str, Any]:
        return self.get(f"/{rtype}/{rid}")

    def search(
        self,
        rtype: str,
        params: dict[str, Any] | None = None,
        max_pages: int = 50,
    ) -> Iterator[dict[str, Any]]:
        """Yield every resource entry across all pages of a search."""
        bundle = self.get(f"/{rtype}", params=params)
        pages = 0
        while True:
            for entry in (bundle.get("entry") or []):
                res = entry.get("resource")
                if res is not None:
                    yield res
            next_url = next(
                (lnk.get("url") for lnk in (bundle.get("link") or [])
                 if lnk.get("relation") == "next"),
                None,
            )
            pages += 1
            if not next_url or pages >= max_pages:
                return
            bundle = self.get(next_url)
