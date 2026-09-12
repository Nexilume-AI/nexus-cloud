"""Operational audit names and explicit distribution-specific additions."""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


CORE_ACTION_ALIASES = {
    "accounts.profile.update": "account.update",
    "tenancy.project.create": "project.create",
    "jobs.create": "job.create",
    "jobs.start": "job.start",
    "jobs.succeed": "job.succeed",
    "jobs.fail": "job.fail",
    "providers.account.create": "provider_account.create",
    "providers.account.remove": "provider_account.remove",
    "deployments.create": "deployment.create",
    "deployments.update": "deployment.update",
    "deployments.disable": "deployment.disable",
    "agents.create": "agent.create",
    "agents.deploy": "agent.deploy",
    "agents.stop": "agent.stop",
    "agents.runtime.image.register": "agent.runtime.image.register",
    "agents.runtime.deploy": "agent.runtime.deploy",
    "agents.runtime.deploy.succeeded": "agent.runtime.deploy.succeeded",
    "agents.runtime.deploy.failed": "agent.runtime.deploy.failed",
    "agents.runtime.stop": "agent.runtime.stop",
    "agents.runtime.invoke": "agent.runtime.invoke",
    "agents.runtime.health.healthy": "agent.runtime.health.healthy",
    "agents.runtime.health.unhealthy": "agent.runtime.health.unhealthy",
    "datasets.create": "dataset.create",
    "datasets.delete": "dataset.delete",
    "datasets.file.download": "dataset.file.download",
    "routers.deploy": "router.deploy",
    "routers.runtime.invoke": "router.runtime.invoke",
    "metrics.alert.create": "alert.create",
}


def configured_action_aliases():
    additional = getattr(settings, "NEXUS_AUDIT_ACTION_ALIASES", None)
    if not isinstance(additional, dict):
        raise ImproperlyConfigured("Nexus audit action catalog is not configured.")
    for legacy, canonical in additional.items():
        if not isinstance(legacy, str) or not legacy or not isinstance(canonical, str) or not canonical:
            raise ImproperlyConfigured("Nexus audit action names must be nonempty strings.")
        if legacy in CORE_ACTION_ALIASES:
            raise ImproperlyConfigured("Nexus audit extensions cannot override core action names.")
    return {**CORE_ACTION_ALIASES, **additional}
