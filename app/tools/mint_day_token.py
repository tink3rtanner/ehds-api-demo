"""Mint a long-lived bearer to paste into a chat, as a shortcut past registration.

Run on the server (it signs with the server's own key):

    python -m app.tools.mint_day_token                 # read only, valid 24 h
    python -m app.tools.mint_day_token --publish       # read + publish (ITI-105)
    python -m app.tools.mint_day_token --hours 8 --label workshop

The token is an ordinary access token for this server, just longer-lived than
the 15-minute ones /token issues. Its client_id is ``day-<label>-<date>`` so its
calls are easy to spot in the audit log. There is no revocation short of
rotating the server key, so keep --hours small.
"""
from __future__ import annotations

import argparse
import time
import uuid

import jwt

from app.auth.jwks import server_kid, server_private_key
from app.config import settings

READ = "system/*.rs"
PUBLISH = "system/DocumentReference.c"


def mint(*, publish: bool, hours: float, label: str) -> tuple[str, str, str]:
    now = int(time.time())
    client_id = f"day-{label}-{time.strftime('%Y%m%d', time.gmtime(now))}"
    scope = f"{READ} {PUBLISH}" if publish else READ
    token = jwt.encode(
        {"iss": settings.issuer, "sub": client_id, "aud": settings.base_url, "iat": now,
         "exp": now + int(hours * 3600), "jti": str(uuid.uuid4()), "scope": scope, "client_id": client_id},
        server_private_key(), algorithm="RS256", headers={"kid": server_kid()},
    )
    return token, client_id, scope


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--publish", action="store_true", help="also allow POST /DocumentReference")
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--label", default="shared")
    a = ap.parse_args()
    if not 0 < a.hours <= 72:
        raise SystemExit("--hours must be between 0 and 72")
    token, client_id, scope = mint(publish=a.publish, hours=a.hours, label=a.label)
    expires = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(time.time() + a.hours * 3600))
    print(f"# client_id {client_id}, scope '{scope}', expires {expires}")
    print(f"# base URL {settings.base_url}; start at {settings.base_url}/llms.txt")
    print(token)


if __name__ == "__main__":
    main()
