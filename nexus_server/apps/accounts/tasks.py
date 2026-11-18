"""Post-cutover verification of device-authorized model transports."""
import logging
from types import SimpleNamespace
from celery import shared_task
from django.core.cache import cache

logger = logging.getLogger(__name__)


def schedule_relay_verification(binding_id, runtime_id):
    # A queue outage must not turn a committed cutover into an HTTP failure.
    # The existing catalog sweep is also armed as a durable recovery path.
    from apps.providers.runtime_services import provider_catalog_refresh_result
    provider_catalog_refresh_result(runtime_id=runtime_id, failed=True)
    try:
        verify_relay_transport.delay(binding_id)
    except Exception:
        logger.warning('Relay verification deferred to catalog maintenance. binding=%s', binding_id)


@shared_task
def verify_relay_transport(binding_id):
    from .models import RelayModelBinding
    from .relay_devices import active, scope_request
    from .relay_transport import allowed_binding
    from apps.providers.connection_services import connection_runtime
    from apps.providers.runtime_services import refresh_runtime_model_offers
    from apps.deployments.models import Deployment
    from apps.deployments.health import check_deployment_health
    binding = RelayModelBinding.objects.select_related('device__user', 'provider').filter(
        pk=binding_id, status='active').first()
    if binding is None:
        return {'skipped': True}
    active(binding.device)
    request = scope_request(SimpleNamespace(META={}, path='/relay/verify', method='POST'), binding.device)
    binding = allowed_binding(binding.device, binding.pk)
    runtime = connection_runtime(binding.provider)
    lock = 'nexus:relay:verify:' + str(binding.pk)
    # Do not delete a lock potentially reacquired after a worker timeout.
    if not cache.add(lock, True, timeout=300):
        return {'skipped_overlap': True}
    # Uses real catalog/inference probes, with existing configuration fences and
    # bounded batches. Unchecked models remain unknown; maintenance retries them.
    refresh_runtime_model_offers(runtime=runtime, actor=request.user, strict=False, bounded_probes=True)
    checked = 0
    for source in Deployment.objects.filter(provider_runtime=runtime, status='active'):
        binding.device.refresh_from_db()
        active(binding.device)
        allowed_binding(binding.device, binding.pk)
        check_deployment_health(deployment=source, request=request, actor=request.user)
        checked += 1
    return {'checked_sources': checked}
