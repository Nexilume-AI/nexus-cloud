"""Start existing real controllers with explicit Personal configuration only."""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def run_controller(kind):
    from .configuration import require_personal_distribution
    require_personal_distribution()
    if kind not in ('agent', 'provider') or kind not in settings.NEXUS_PERSONAL_CONFIGURED_CONTROLLERS:
        raise ImproperlyConfigured('PERSONAL_CONTROLLER_UNCONFIGURED: provision the protected controller configuration first.')
    if settings.NEXUS_PROCESS_ROLE != kind + '-controller':
        raise ImproperlyConfigured('Start the controller through nexus_personal.processes in a dedicated process.')
    if kind == 'agent' and not settings.NEXUS_AGENT_RUNTIME_EGRESS_POLICY_READY:
        raise ImproperlyConfigured('AGENT_EGRESS_POLICY_REQUIRED: provision and verify Docker worker egress filtering first.')
    # Reuse the established command's dependency verification and real server.
    # Neither a successful ping nor a fake runner substitutes for Docker checks.
    from .manage import main as manage
    import sys
    sys.argv = ['personal-manage', f'run_{kind}_runtime_controller']
    return manage()
