"""Merge only managed settings; never truncate a user's runtime configuration."""
import os
import tempfile
import uuid
from pathlib import Path

import yaml
from rest_framework import exceptions


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError("Duplicate configuration key")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _atomic_write(path: Path, data: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def merge_runtime_config(path: Path, managed: dict, *, defaults: dict | None = None) -> bool:
    try:
        if path.is_symlink():
            raise ValueError("Configuration must be a regular file")
        original = path.read_bytes() if path.exists() else None
        current = yaml.load(original.decode("utf-8-sig"), Loader=UniqueKeyLoader) if original else {}
        if not isinstance(current, dict):
            raise ValueError("Configuration must be a mapping")
        previous = yaml.safe_dump(current, sort_keys=False)
        for key, value in (defaults or {}).items():
            current.setdefault(key, value)

        def merge(target, source):
            for key, value in source.items():
                if isinstance(value, dict):
                    if key not in target:
                        target[key] = {}
                    if not isinstance(target[key], dict):
                        raise ValueError("Managed section must be a mapping")
                    merge(target[key], value)
                elif key == "api-keys":
                    existing = target.get(key, [])
                    if not isinstance(existing, list) or any(not isinstance(x, str) for x in existing):
                        raise ValueError("API keys must be a string list")
                    target[key] = list(dict.fromkeys([*value, *existing]))
                else:
                    target[key] = value

        merge(current, managed)
        rendered = yaml.safe_dump(current, sort_keys=False, allow_unicode=True)
        if original is not None and rendered == previous:
            return False  # Keep formatting and comments intact when nothing managed changed.
        if original is not None:
            _atomic_write(path.with_name(f"{path.name}.backup-{uuid.uuid4().hex}"), original)
            if path.read_bytes() != original:
                raise ValueError("Configuration changed during preparation")
        _atomic_write(path, rendered.encode("utf-8"))
        return True
    except Exception:
        # Parser errors can include credentials from the source document.
        raise exceptions.ValidationError(
            "Runtime configuration could not be safely prepared. Review its format, storage permissions and preserved configuration backup before retrying."
        ) from None
