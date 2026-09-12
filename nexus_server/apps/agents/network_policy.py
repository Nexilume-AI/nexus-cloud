"""Optional host-selected network contract; legacy runners retain their policy."""
from django.conf import settings
from django.utils.module_loading import import_string
from rest_framework.exceptions import APIException


class _Policy:
    def __init__(self, backend):
        self.backend = backend

    def _call(self, method, *args):
        try:
            return getattr(self.backend, method)(*args)
        except (OSError, RuntimeError, ValueError):
            raise APIException('AGENT_EGRESS_POLICY_UNAVAILABLE: restore the worker network policy or redeploy incompatible containers.') from None

    def prepare(self, network, labels):
        return self._call('prepare', network, labels)

    def validate_network(self, info, network, labels):
        return self._call('validate_network', info, network, labels)

    def validate_container(self, info, network):
        return self._call('validate_container', info, network)


def selected_policy():
    backend = getattr(settings, 'NEXUS_AGENT_NETWORK_POLICY_FACTORY', '')
    if not backend:
        return None
    try:
        return _Policy(import_string(backend)(settings))
    except (OSError, RuntimeError, ValueError, ImportError, AttributeError):
        raise APIException('AGENT_EGRESS_POLICY_UNAVAILABLE: worker network policy is not configured correctly.') from None
