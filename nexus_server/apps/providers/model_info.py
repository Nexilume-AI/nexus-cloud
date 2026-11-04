"""Best-effort, read-only upstream metadata; never a model-health decision."""
import json
from urllib.request import Request, build_opener
from apps.gateway.models_dev import NoRedirect
from apps.gateway.model_metadata import normalize, merge_fields, aggregate


def read_optional_info(base_url, headers):
    try:
        base = base_url.rstrip("/")
        for suffix in ("/models", "/chat/completions"):
            if base.endswith(suffix):
                base = base[:-len(suffix)]
        request = Request(base + "/model/info", headers=headers, method="GET")
        with build_opener(NoRedirect).open(request, timeout=5) as response:
            body = response.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024:
            return {}
        rows = json.loads(body).get("data")
        if not isinstance(rows, list) or len(rows) > 10000:
            return {}
        grouped = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = row.get("model_name", row.get("id"))
            if isinstance(name, str):
                grouped.setdefault(name, []).append(merge_fields([(normalize(row), "upstream", None, False)]))
        # LiteLLM may list multiple deployments of one alias. Never let a last
        # larger deployment overwrite the safe capacity of its siblings.
        return {name: {key: field["value"] for key, field in aggregate(candidates).items()}
                for name, candidates in grouped.items()}
    except Exception:
        return {}


def retain_capacity(offer, runtime):
    from apps.gateway.model_metadata import endpoint_hash
    from django.utils import timezone
    values = getattr(runtime, "_discovered_model_capacities", {}).get(offer.upstream_model_id)
    if values and any(value is not None for value in values.values()):
        offer.metadata = {**(offer.metadata or {}), "model_capacity": {
            "values": values, "updated_at": timezone.now().isoformat(),
            "endpoint_hash": endpoint_hash(runtime.internal_api_url)}}
