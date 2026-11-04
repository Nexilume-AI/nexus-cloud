"""Local last-known-good models.dev snapshot, refreshed outside request handling."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.conf import settings
from .model_metadata import mapping, normalize

MAX_BYTES = 32 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def cache_path():
    return Path(getattr(settings, "NEXUS_MODELS_DEV_CACHE_PATH", "") or
                Path(settings.BASE_DIR) / "storage" / "model-info" / "models-dev.json")


def snapshot():
    try:
        with cache_path().open("rb") as stream:
            body = stream.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            return {}
        result = json.loads(body)
        return result if result.get("schema") == 1 and isinstance(result.get("providers"), dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def refresh():
    # Fixed public destination; never send a Provider credential to this service.
    request = Request("https://models.dev/api.json", headers={"Accept": "application/json", "User-Agent": "Nexus-Model-Catalog/1"})
    with build_opener(NoRedirect).open(request, timeout=15) as response:
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("Model catalog exceeds size limit")
    raw = json.loads(body)
    if not isinstance(raw, dict) or not raw or len(raw) > 2000:
        raise ValueError("Invalid model catalog")
    providers = {}
    for provider_id, provider in raw.items():
        models = mapping(mapping(provider).get("models"))
        if not isinstance(provider_id, str) or len(provider_id) > 200 or len(models) > 50000:
            raise ValueError("Invalid provider catalog")
        cleaned = {model_id: normalize(model) for model_id, model in models.items()
                   if isinstance(model_id, str) and 0 < len(model_id) <= 255 and isinstance(model, dict)}
        if cleaned:
            providers[provider_id] = cleaned
    if not providers or not any(any(any(row.get(k) for k in ("context_window", "max_input_tokens", "max_output_tokens"))
                                  for row in models.values()) for models in providers.values()):
        raise ValueError("Model catalog contains no capacity data")
    data = {"schema": 1, "updated_at": datetime.now(timezone.utc).isoformat(), "providers": providers}
    encoded = json.dumps(data).encode()
    if len(encoded) > MAX_BYTES:
        raise ValueError("Normalized catalog exceeds size limit")
    target = cache_path(); target.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix="models-dev-", suffix=".tmp", delete=False) as stream:
            name = stream.name; stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, target)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)
    return {"providers": len(providers), "models": sum(len(rows) for rows in providers.values())}


def lookup(data, provider_id, model_id):
    # Exact matches only. An explicit source mapping may name a different ID.
    return mapping(mapping(mapping(data.get("providers")).get(provider_id)).get(model_id))
