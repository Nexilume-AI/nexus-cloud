"""Required host policy for Gateway credentials, candidate access and effects."""
from importlib import import_module
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


OPERATIONS = ('resolve_deployment_candidates', 'get_router_for_payload', 'model_group_source_trace', 'model_group_source_exclusion_reason', 'enforce_api_key_policy', 'enforce_api_key_router_policy', 'apply_single_router_key_default', 'write_failure_log', 'pool_deployments_for_model', 'pool_deployments_for_router', 'active_pool_contributions', 'deployments_from_contributions', 'pool_contribution_has_capacity', 'pool_tokens_since', 'pool_reserved_tokens_since', 'reserve_provider_capacity_for_deployment', 'pool_contribution_for_deployment', 'record_success_pool_usage', 'record_failed_pool_usage_for_deployment', 'count_previous_provider_failures', 'mark_provider_capacity_used', 'release_provider_capacity', 'authorize_router')


def gateway_integration():
    path = getattr(settings, "NEXUS_GATEWAY_INTEGRATION_MODULE", "")
    try:
        if not isinstance(path, str) or not path:
            raise ValueError()
        module = import_module(path)
    except (ImportError, ValueError, TypeError):
        raise ImproperlyConfigured("Gateway integration is not configured.") from None
    if not all(callable(getattr(module, name, None)) for name in (*OPERATIONS, "observe_stream", "complete_stream", "openai_model_catalog", "claude_model_catalog", "response_events", "prepare_request", "images", "image_response")):
        raise ImproperlyConfigured("Gateway integration is incomplete.")
    return module
