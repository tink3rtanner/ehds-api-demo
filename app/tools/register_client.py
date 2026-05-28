"""Register a SMART backend services client.

usage:
  # generate a keypair locally + register (most common):
  python -m app.tools.register_client --client-id my-app --generate --scope "system/*.read"

  # register a remote server (REST path; no local server access needed):
  python -m app.tools.register_client --client-id my-app --generate \\
      --base-url https://ehds.joshpriebe.com

  # use an existing public-key PEM file:
  python -m app.tools.register_client --client-id my-app --jwk-from-pem ./pub.pem

  # pipe a PEM in (AI-native: agent generates key, pipes it):
  cat pub.pem | python -m app.tools.register_client --client-id my-app --jwk-from-stdin

  # machine-readable JSON output:
  python -m app.tools.register_client --client-id my-app --generate --out json

modes:
  - local registry write: writes into $EHDS_CLIENT_REGISTRY (default
    config/clients.json). use this when running on the same host as the server.
  - REST registration (`--base-url`): POSTs to {base}/ui/api/register-client.
    use this when the agent doesn't have file access to the server.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def _pubpem_to_jwk(pem_bytes: bytes, kid: str) -> dict:
    from jwt.algorithms import RSAAlgorithm
    pub = serialization.load_pem_public_key(pem_bytes)
    jwk = json.loads(RSAAlgorithm.to_jwk(pub))
    jwk["kid"] = kid
    jwk["use"] = "sig"
    jwk["alg"] = "RS256"
    return jwk


def _generate(client_id: str, out_dir: Path = Path(".")) -> tuple[dict, Path, Path]:
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_path = out_dir / f"client-{client_id}.pem"
    pub_path = out_dir / f"client-{client_id}.pub.pem"
    priv_path.write_bytes(priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    priv_path.chmod(0o600)
    pub_path.write_bytes(priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
    return _pubpem_to_jwk(pub_path.read_bytes(), f"{client_id}-key-1"), priv_path, pub_path


def _register_local(client_id: str, jwks: dict, scopes: list[str]) -> dict:
    from app.auth.jwks import upsert_client
    upsert_client(client_id, jwks, scopes)
    return {"mode": "local", "client_id": client_id, "scopes": scopes, "jwks": jwks}


def _register_remote(base_url: str, client_id: str, public_pem: bytes, scopes: list[str]) -> dict:
    import urllib.error
    import urllib.request
    payload = json.dumps({
        "client_id": client_id,
        "scopes": scopes,
        "public_key_pem": public_pem.decode(),
    }).encode()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/ui/api/register-client",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return {"mode": "remote", **json.loads(r.read())}
    except urllib.error.HTTPError as e:
        sys.exit(f"remote registration failed: HTTP {e.code} {e.read().decode()[:200]}")
    except urllib.error.URLError as e:
        sys.exit(f"remote registration failed: {e}")


def main():
    ap = argparse.ArgumentParser(
        description="Register a SMART backend services client (locally or via REST).",
    )
    ap.add_argument("--client-id", required=True, help="lowercase letters/numbers/hyphens")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--jwk-from-pem", type=Path, help="PEM-encoded public key file")
    src.add_argument("--jwk-from-stdin", action="store_true", help="read PEM from stdin")
    src.add_argument("--jwk-json", help="inline JWKS JSON string")
    src.add_argument("--generate", action="store_true", help="generate a keypair locally")
    ap.add_argument("--scope", action="append", default=[],
                    help="scope to grant (repeatable, default: system/*.read)")
    ap.add_argument("--base-url", default=None,
                    help="if set, register against the running server's REST API instead "
                         "of writing to the local registry. example: https://ehds.joshpriebe.com")
    ap.add_argument("--out", choices=("text", "json"), default="text",
                    help="output format; 'json' is machine-readable for AI agents")
    ap.add_argument("--keypair-dir", type=Path, default=Path("."),
                    help="where to write generated keypair files")
    args = ap.parse_args()

    if not args.scope:
        args.scope = ["system/*.read"]

    keypair_files = None
    if args.jwk_from_pem:
        pem = args.jwk_from_pem.read_bytes()
        jwk = _pubpem_to_jwk(pem, f"{args.client_id}-key-1")
        jwks = {"keys": [jwk]}
    elif args.jwk_from_stdin:
        pem = sys.stdin.buffer.read()
        jwk = _pubpem_to_jwk(pem, f"{args.client_id}-key-1")
        jwks = {"keys": [jwk]}
    elif args.jwk_json:
        parsed = json.loads(args.jwk_json)
        jwks = parsed if "keys" in parsed else {"keys": [parsed]}
        pem = None
    else:  # generate
        jwk, priv_path, pub_path = _generate(args.client_id, args.keypair_dir)
        jwks = {"keys": [jwk]}
        keypair_files = {"private_key": str(priv_path), "public_key": str(pub_path)}
        pem = pub_path.read_bytes()

    if args.base_url:
        if pem is None:
            sys.exit("--base-url requires a PEM (use --generate, --jwk-from-pem, or --jwk-from-stdin)")
        result = _register_remote(args.base_url, args.client_id, pem, args.scope)
    else:
        result = _register_local(args.client_id, jwks, args.scope)

    if keypair_files:
        result["keypair_files"] = keypair_files

    if args.out == "json":
        print(json.dumps(result, indent=2))
    else:
        print(f"✓ registered client {args.client_id!r} ({result['mode']})")
        if keypair_files:
            print(f"  private key: {keypair_files['private_key']}  (0600)")
            print(f"  public key:  {keypair_files['public_key']}")
        print(f"  scopes: {args.scope}")
        if result.get("next_steps"):
            n = result["next_steps"]
            print(f"  next: sign JWT with kid={n['client_assertion_kid']!r} aud={n['audience']!r}")


if __name__ == "__main__":
    main()
