"""Pure shared build-time composition, without a Cloud bootstrap dependency.

A distribution supplies explicit contributions. Duplicates, missing anchors and
setting/schedule collisions are errors, never last-writer-wins overrides.
"""
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Mapping


class DistributionConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class Insertion:
    before: str
    items: tuple[str, ...]


@dataclass(frozen=True)
class DistributionExtension:
    name: str
    applications: tuple[Insertion, ...] = ()
    api_routes: tuple[Insertion, ...] = ()
    settings: Mapping = field(default_factory=dict)
    periodic_tasks: Mapping = field(default_factory=dict)


def _insert(base, contributions):
    result = list(base)
    if len(result) != len(set(result)):
        raise DistributionConfigurationError("Duplicate base registration.")
    for contribution in contributions:
        if contribution.before not in result:
            raise DistributionConfigurationError("Distribution registration anchor is missing.")
        if not contribution.items or any(not isinstance(item, str) or not item for item in contribution.items):
            raise DistributionConfigurationError("Invalid distribution registration.")
        if len(set(contribution.items)) != len(contribution.items) or set(result) & set(contribution.items):
            raise DistributionConfigurationError("Duplicate distribution registration.")
        index = result.index(contribution.before)
        result[index:index] = contribution.items
    return result


def compose_settings(base: Mapping, extension: DistributionExtension) -> dict:
    """Return new settings only after validating every contribution; don't mutate base."""
    if not extension.name or not isinstance(extension.name, str):
        raise DistributionConfigurationError("Distribution name is required.")
    reserved = {"INSTALLED_APPS", "NEXUS_API_ROUTES", "CELERY_BEAT_SCHEDULE", "NEXUS_DISTRIBUTION"}
    if any(not isinstance(key, str) or not key.startswith("NEXUS_") or key in reserved or key in base
           for key in extension.settings):
        raise DistributionConfigurationError("Distribution settings conflict with base settings.")
    applications = _insert(base["INSTALLED_APPS"], extension.applications)
    routes = list(base["NEXUS_API_ROUTES"])
    route_prefixes = dict((module, prefix) for prefix, module in routes)
    route_modules = _insert([module for _, module in routes], extension.api_routes)
    schedule = deepcopy(base["CELERY_BEAT_SCHEDULE"])
    if set(schedule) & set(extension.periodic_tasks):
        raise DistributionConfigurationError("Distribution periodic task name conflicts with base.")
    for name, task in extension.periodic_tasks.items():
        if not isinstance(name, str) or not name or not isinstance(task, dict) or not task.get("task") or "schedule" not in task:
            raise DistributionConfigurationError("Invalid distribution periodic task.")
    schedule.update(deepcopy(dict(extension.periodic_tasks)))
    return {
        **base, **deepcopy(dict(extension.settings)),
        "NEXUS_DISTRIBUTION": extension.name,
        "INSTALLED_APPS": applications,
        "NEXUS_API_ROUTES": tuple((route_prefixes.get(module, "api/v1/"), module) for module in route_modules),
        "CELERY_BEAT_SCHEDULE": schedule,
    }
