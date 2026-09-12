"""Header extraction only; every delegate verifies its own scoped credential."""


def interaction_token(request) -> str:
    return request.headers.get("X-Nexus-Interaction-Token") or ""
