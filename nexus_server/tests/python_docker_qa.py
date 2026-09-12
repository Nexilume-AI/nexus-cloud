"""Explicit immutable QA profile selection and exact owned-image cleanup.

These helpers never select a production policy, retag the shared profile, or
weaken the builder's offline/source verification and deployment guards.
"""
import json
from contextlib import contextmanager
import os
import re
import subprocess
from uuid import UUID


@contextmanager
def pinned_profile_alias(executable, reference, build_id):
    """A per-test FROM alias; never rewrite the shared operator profile tag."""
    alias = 'nexus-python-cleanup-base:' + UUID(str(build_id)).hex
    rows = json.loads(_command(executable, 'image', 'inspect', reference))
    if len(rows) != 1 or not re.fullmatch(r'sha256:[a-f0-9]{64}', rows[0].get('Id', '')):
        raise ValueError('Docker QA profile identity invalid')
    digest = rows[0]['Id']
    if _command(executable, 'image', 'ls', '--quiet', '--filter', 'reference=' + alias).strip():
        raise ValueError('Docker QA alias already exists')
    _command(executable, 'tag', digest, alias)
    try:
        yield alias
    finally:
        current = json.loads(_command(executable, 'image', 'inspect', alias))
        if len(current) != 1 or current[0].get('Id') != digest or alias not in (current[0].get('RepoTags') or []):
            raise ValueError('Docker QA alias ownership mismatch')
        _command(executable, 'image', 'rm', '--no-prune', alias)


def _command(executable, *arguments):
    result = subprocess.run([executable, *arguments], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('DOCKER_QA_COMMAND_FAILED')
    return result.stdout


def resolve_profile(executable):
    explicit = os.environ.get('NEXUS_PYTHON_DOCKER_QA_BASE_IMAGE')
    if explicit is not None and not re.fullmatch(r'sha256:[a-f0-9]{64}', explicit):
        raise ValueError('Docker QA requires an explicit immutable local image ID')
    reference = explicit or 'nexus-python-profile:qa-20260831'
    rows = json.loads(_command(executable, 'image', 'inspect', reference))
    if not isinstance(rows, list) or len(rows) != 1:
        raise ValueError('Docker QA profile resolution is ambiguous')
    digest = rows[0].get('Id', '')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', digest) or (explicit and explicit != digest):
        raise ValueError('Docker QA profile digest mismatch')
    return digest


def remove_owned_image(executable, build_id, agent_id):
    # Caller records its UUIDs BEFORE building; even a failed verification can
    # leave an image. Never clean arbitrary images from a global before/after diff.
    identity, agent = UUID(str(build_id)).hex, UUID(str(agent_id)).hex
    tag = f'nexus-python/{agent}:{identity}'
    present = _command(executable, 'image', 'ls', '--quiet', '--filter', 'reference=' + tag).strip()
    if not present:
        return
    rows = json.loads(_command(executable, 'image', 'inspect', tag))
    if (len(rows) != 1 or tag not in (rows[0].get('RepoTags') or [])
            or (rows[0].get('Config', {}).get('Labels') or {}).get('nexus.managed') != 'python-build'):
        raise ValueError('Docker QA image ownership mismatch')
    # Remove only this fixture tag; never force-remove a shared digest/container.
    _command(executable, 'image', 'rm', tag)
