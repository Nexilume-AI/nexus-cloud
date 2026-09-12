#!/usr/bin/env python3
"""Create or reuse a durable local VAPID signing key without printing it."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _write_private(path: Path, value: bytes) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(value)
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(path)


def _load_or_create(path: Path) -> ec.EllipticCurvePrivateKey:
    if path.exists():
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
            raise ValueError("The stored VAPID key is not an EC P-256 private key.")
        return key
    key = ec.generate_private_key(ec.SECP256R1())
    _write_private(
        path,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    return key


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--subject", default="mailto:nexus@localhost")
    args = parser.parse_args()
    if not args.subject.startswith(("mailto:", "https://")):
        parser.error("--subject must use mailto: or https://")

    state = Path(args.state_dir).resolve()
    state.mkdir(parents=True, exist_ok=True)
    private_path = state / "vapid-private.pem"
    key = _load_or_create(private_path)
    public_bytes = key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    public_key = base64.urlsafe_b64encode(public_bytes).rstrip(b"=").decode("ascii")
    # Only the path is emitted. Private key material never enters stdout,
    # launcher logs, process arguments, or the generated public manifest.
    print(json.dumps({
        "public_key": public_key,
        "private_key_file": str(private_path),
        "subject": args.subject,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
