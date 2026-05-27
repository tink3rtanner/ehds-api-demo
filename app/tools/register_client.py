"""Register a SMART backend services client.

usage:
  python -m app.tools.register_client --client-id <id> --jwk-from-pem path/to/pub.pem [--scope ...]
  python -m app.tools.register_client --client-id <id> --jwk-json '{"keys":[...]}'
  python -m app.tools.register_client --client-id <id> --generate  # mint a keypair, save to ./client-<id>.{pem,pub.pem}

writes into $EHDS_CLIENT_REGISTRY (defaults to config/clients.json).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth.jwks import upsert_client


def _pubpem_to_jwk(pem_bytes: bytes, kid: str) -> dict:
    from jwt.algorithms import RSAAlgorithm
    pub = serialization.load_pem_public_key(pem_bytes)
    jwk = json.loads(RSAAlgorithm.to_jwk(pub))
    jwk["kid"] = kid
    jwk["use"] = "sig"
    jwk["alg"] = "RS256"
    return jwk


def _generate(client_id: str) -> dict:
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_path = Path(f"./client-{client_id}.pem")
    pub_path = Path(f"./client-{client_id}.pub.pem")
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
    print(f"keypair written: {priv_path} (private, 0600), {pub_path} (public)")
    return _pubpem_to_jwk(pub_path.read_bytes(), f"{client_id}-key-1")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-id", required=True)
    ap.add_argument("--jwk-from-pem", type=Path, help="PEM-encoded public key file")
    ap.add_argument("--jwk-json", help="inline JWKS JSON string")
    ap.add_argument("--generate", action="store_true", help="generate a keypair locally")
    ap.add_argument("--scope", action="append", default=[],
                    help="scope to grant (repeatable, default: system/*.read)")
    args = ap.parse_args()

    if not args.scope:
        args.scope = ["system/*.read"]

    if sum(map(bool, [args.jwk_from_pem, args.jwk_json, args.generate])) != 1:
        sys.exit("specify exactly one of --jwk-from-pem, --jwk-json, --generate")

    if args.jwk_from_pem:
        jwk = _pubpem_to_jwk(args.jwk_from_pem.read_bytes(), f"{args.client_id}-key-1")
        jwks = {"keys": [jwk]}
    elif args.jwk_json:
        parsed = json.loads(args.jwk_json)
        jwks = parsed if "keys" in parsed else {"keys": [parsed]}
    else:
        jwk = _generate(args.client_id)
        jwks = {"keys": [jwk]}

    upsert_client(args.client_id, jwks, args.scope)
    print(f"registered client {args.client_id!r} with scopes {args.scope}")


if __name__ == "__main__":
    main()
