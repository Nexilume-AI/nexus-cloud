"""Presentation of operator-declared transport; never an authorization signal."""
from urllib.parse import urlsplit
from django.conf import settings


def endpoint_identity(value):
    try:
        url = urlsplit(value or "")
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
            return None
        return (url.scheme, url.hostname.lower(), url.port or (443 if url.scheme == "https" else 80), url.path.rstrip("/"))
    except (ValueError, TypeError):
        return None


def connection_transport(account, runtime):
    from apps.accounts.models import RelayModelBinding
    binding = None
    prefix = str(getattr(settings, 'NEXUS_PUBLIC_BASE_URL', '')).rstrip('/') + '/api/model-relay/bindings/'
    if getattr(account, 'pk', None) and str(account.url).startswith(prefix):
        binding = RelayModelBinding.objects.select_related('device').filter(provider_id=account.pk, status='active').first()
    if binding:
        from apps.accounts.relay_transport import endpoint
        if endpoint_identity(account.url) == endpoint_identity(endpoint(binding)):
            return {'mode': 'relay', 'relay_name': binding.device.name}
    # No hostname/name heuristics: a URL containing 'relay' is not evidence.
    engine = getattr(runtime, "runtime_type", "direct_api")
    endpoint = account.url if engine == "direct_api" else getattr(runtime, "internal_api_url", "")
    identity = endpoint_identity(endpoint)
    registry = getattr(settings, "NEXUS_PROVIDER_RELAY_ENDPOINTS", {})
    if identity and isinstance(registry, dict):
        for url, label in registry.items():
            if identity == endpoint_identity(url) and isinstance(label, str) and 0 < len(label) <= 100:
                return {"mode": "relay", "relay_name": label}
    return {"mode": "managed" if engine != "direct_api" else "direct" if identity else "unknown", "relay_name": ""}
