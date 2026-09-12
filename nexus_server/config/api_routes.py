"""Owned-resource API registrations; distribution extensions supply other APIs.

This inventory still has transitive commercial dependencies and is not a
standalone Community URL configuration yet.
"""
CORE_API_ROUTES = (
    ("api/v1/auth/", "apps.accounts.urls"),
    ("api/v1/account/", "apps.accounts.account_urls"),
    ("api/v1/", "apps.notifications.urls"),
    ("api/v1/", "apps.tenancy.urls"),
    ("api/v1/", "apps.jobs.urls"),
    ("api/v1/", "apps.providers.urls"),
    ("api/v1/", "apps.deployments.urls"),
    ("api/v1/", "apps.routers.urls"),
    ("api/v1/", "apps.gateway.urls"),
    ("api/v1/", "apps.agents.urls"),
    ("api/v1/", "apps.datasets.urls"),
    ("api/v1/", "apps.metrics.urls"),
    ("api/v1/", "apps.audit.urls"),
    ("api/v1/", "apps.workspaces.urls"),
    ("api/v1/", "apps.mobile.urls"),
)
