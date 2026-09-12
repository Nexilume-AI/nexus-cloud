"""Operator-key approval of exact local Docker image IDs; no registry or signing.

Sign the raw UTF-8 payload bytes with Ed25519, then base64-encode those same bytes
and the detached signature into an envelope. Private keys never enter this worker.
This verifies an administrator approval, not scanner results or build provenance.
"""
import argparse
import base64
import binascii
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from django.core.exceptions import ImproperlyConfigured

from .host_config import read_protected_json


DIGEST = re.compile(r'sha256:[a-f0-9]{64}')
HOST = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}')
PURPOSE = 'nexus-agent-image-admission-v1'
MAX_LIFETIME = 7 * 86400
MAX_ENVELOPE = 8192


class AdmissionRejected(ValueError):
    """Only constant error codes, never untrusted input or filesystem paths."""


def reject():
    raise AdmissionRejected('AGENT_SIGNED_APPROVAL_REJECTED') from None


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            reject()
        result[key] = value
    return result


def _json(raw):
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                           parse_constant=lambda _: reject())
    except (ValueError, UnicodeError, RecursionError):
        reject()
    if not isinstance(value, dict):
        reject()
    return value


def _base64(value, *, size=None, maximum=4096):
    if not isinstance(value, str) or len(value) > maximum * 2:
        reject()
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        reject()
    if (len(raw) > maximum or (size is not None and len(raw) != size)
            or base64.b64encode(raw).decode('ascii') != value):
        reject()
    return raw


def verify_envelope(raw, *, public_key, image_id, host_id, now=None):
    """Pure verification; public_key and expected identities come from the operator."""
    if (not isinstance(raw, bytes) or len(raw) > MAX_ENVELOPE
            or not isinstance(image_id, str) or not DIGEST.fullmatch(image_id)
            or not isinstance(host_id, str) or not HOST.fullmatch(host_id)):
        reject()
    envelope = _json(raw)
    if set(envelope) != {'payload', 'signature'}:
        reject()
    payload_bytes = _base64(envelope['payload'])
    signature = _base64(envelope['signature'], size=64)
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, payload_bytes)
    except (InvalidSignature, ValueError, TypeError):
        reject()
    payload = _json(payload_bytes)
    if (set(payload) != {'purpose', 'image_id', 'host_id', 'issued_at', 'expires_at'}
            or payload['purpose'] != PURPOSE or payload['image_id'] != image_id
            or payload['host_id'] != host_id):
        reject()
    issued, expires = payload['issued_at'], payload['expires_at']
    current = int(time.time()) if now is None else now
    if (type(issued) is not int or type(expires) is not int
            or type(current) is not int or not 0 <= issued <= current < expires
            or not 0 < expires - issued <= MAX_LIFETIME):
        reject()


def _absolute_unlinked(value, *, directory=False):
    if not isinstance(value, str):
        reject()
    path = Path(value)
    if not path.is_absolute():
        reject()
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            reject()
    if directory and not path.is_dir():
        reject()
    return path


def verify_approval(*, trust_config, image_id, host_id):
    """Re-read protected policy on every attempt, including key/digest revocation."""
    try:
        if not isinstance(image_id, str) or not DIGEST.fullmatch(image_id):
            reject()
        policy_path = _absolute_unlinked(str(trust_config))
        policy = read_protected_json(str(policy_path))
        if (not isinstance(policy, dict) or set(policy) != {
                'schema_version', 'host_id', 'public_key', 'approvals_dir', 'revoked_image_ids'}
                or type(policy['schema_version']) is not int or policy['schema_version'] != 1
                or policy['host_id'] != host_id):
            reject()
        revoked = policy['revoked_image_ids']
        if (not isinstance(revoked, list) or len(revoked) > 256
                or any(not isinstance(x, str) or not DIGEST.fullmatch(x) for x in revoked)
                or image_id in revoked):
            reject()
        public_key = _base64(policy['public_key'], size=32)
        directory = _absolute_unlinked(policy['approvals_dir'], directory=True)
        receipt = _absolute_unlinked(str(directory / (image_id[7:] + '.json')))
        descriptor = os.open(receipt, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                             | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ENVELOPE:
                reject()
            raw = stream.read(MAX_ENVELOPE + 1)
        verify_envelope(raw, public_key=public_key, image_id=image_id, host_id=host_id)
    except (OSError, ValueError, TypeError, ImproperlyConfigured):
        reject()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trust-config', required=True)
    parser.add_argument('--host-id', required=True)
    parser.add_argument('image_id')  # Appended by the existing Docker runner.
    args = parser.parse_args(argv)
    try:
        verify_approval(trust_config=args.trust_config, image_id=args.image_id, host_id=args.host_id)
    except AdmissionRejected:
        print('AGENT_SIGNED_APPROVAL_REJECTED', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
