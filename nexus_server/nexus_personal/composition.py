"""Explicit personal product composition, shared by hosts and isolation tests.

No environment, test secrets, database selection or commercial fallbacks live here.
"""
INSTALLED_APPS = [
    "django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions",
    "rest_framework", "rest_framework.authtoken", "apps.accounts", "apps.audit",
    "apps.tenancy", "nexus_personal.apps.PersonalConfig", "apps.datasets",
    "apps.workspaces", "apps.mobile", "apps.agents", "apps.jobs", "apps.notifications",
    "apps.providers", "apps.deployments", "apps.routers", "apps.gateway",
    "apps.metrics.apps.MetricsConfig",
]
MIGRATION_MODULES = {"tenancy": "nexus_personal.context_migrations", "datasets": "nexus_personal.dataset_migrations"}
MIGRATION_MODULES.update({
    "agents": "nexus_personal.agent_migrations",
    "workspaces": "nexus_personal.workspace_migrations",
    "mobile": "nexus_personal.mobile_migrations",
    "jobs": "nexus_personal.job_migrations",
    "notifications": "nexus_personal.notification_migrations",
    "providers": "nexus_personal.provider_migrations",
    "deployments": "nexus_personal.deployment_migrations",
    "routers": "nexus_personal.router_migrations",
    "gateway": "nexus_personal.gateway_migrations",
    "metrics": "nexus_personal.metrics_migrations",
})
# The shared Tool Setup schema uses only this host's restricted credentials.
NEXUS_WORKSPACE_MODEL_MODULES = ("apps.workspaces.tool_models",)
NEXUS_WORKSPACE_TOOL_MODEL_EXTENSION = "nexus_personal.tool_models"
NEXUS_TOOL_SETUP_MODULE = "nexus_personal.tool_setup"
NEXUS_WORKSPACE_CONTEXT_BACKEND = "nexus_personal.workspace_policy.PersonalWorkspaceContext"
NEXUS_DISTRIBUTION = "community"
NEXUS_AUDIT_ACTION_ALIASES = {}
NEXUS_AGENT_EXTRA_TASK_MODULES = ()
NEXUS_NOTIFICATION_BACKEND = "nexus_personal.notifications.PersonalNotificationBackend"
NEXUS_IDENTITY_BACKEND = "nexus_personal.identity.DatabasePersonalIdentityBackend"
NEXUS_REQUEST_CONTEXT_BACKEND = "nexus_personal.request_context.PersonalRequestContext"
NEXUS_TENANCY_SERVICES_MODULE = "nexus_personal.tenancy_services"
NEXUS_MONITORING_POLICY_MODULE = "nexus_personal.monitoring_policy"
NEXUS_MONITORING_HTTP_MODULE = "nexus_personal.monitoring_http"
NEXUS_MONITORING_COMPONENTS = {
    "services": "nexus_personal.monitoring_http",
    "registry": "nexus_personal.monitoring_registry",
    "views": "apps.metrics.operational_views",
    "urls": "nexus_personal.monitoring_urls",
    "local": "nexus_personal.monitoring_local",
    "tasks": "apps.metrics.operational_tasks",
}
NEXUS_MONITORING_WORKER_MODULE = "nexus_personal.monitoring_workers"
NEXUS_MONITORING_MODEL_EXTENSION = "nexus_personal.monitoring_models"
NEXUS_MONITORING_WINDOW_FUNCTION = "nexus_personal.monitoring_window.window_metrics"
NEXUS_RESOURCE_ADMISSION_BACKEND = "nexus_personal.resource_limits.PersonalResourceAdmission"
NEXUS_PERSONAL_DATA_LIMITS = {
    "data.collections": 100, "data.files": 1000, "data.storage_gb": 10,
    "data.export_gb_per_30_days": 20,
}
NEXUS_DATASET_STORAGE_BACKEND = "local"
NEXUS_DATASET_SEARCH_BACKEND = "database"
NEXUS_RESOURCE_CATALOG_BACKEND = "nexus_personal.resource_catalog.PersonalResourceCatalog"
NEXUS_PROVIDER_CATALOG_BACKEND = "nexus_personal.provider_catalog.PersonalProviderCatalog"
NEXUS_PROVIDER_RUNTIME_INTEGRATION = "nexus_personal.provider_runtime_integration.PersonalProviderRuntimeIntegration"
NEXUS_PROVIDER_CONNECTION_SERIALIZER = "nexus_personal.provider_serializers.PersonalProviderConnectionSerializer"
# Real Source health and inference tests never use the Gateway's mock hosts.
NEXUS_GATEWAY_MOCK_HOSTS = ()
NEXUS_GATEWAY_FAIL_HOSTS = ()
NEXUS_PERSONAL_MODEL_LIMITS = {"models.provider_connections": 100, "models.model_offers": 1000,
                               "models.execution_routers": 100, "models.aggregation_routers": 100}
NEXUS_PROVIDER_MODEL_EXTENSION = "nexus_personal.provider_models"
NEXUS_DEPLOYMENT_MODEL_EXTENSION = "nexus_personal.provider_models"
NEXUS_ROUTER_MODEL_EXTENSION = "nexus_personal.router_models"
NEXUS_GATEWAY_MODEL_EXTENSION = "nexus_personal.gateway_models"
NEXUS_GATEWAY_INTEGRATION_MODULE = "nexus_personal.gateway_integration"
NEXUS_GATEWAY_LIFECYCLE_BACKEND = "nexus_personal.gateway_lifecycle.PersonalGatewayLifecycle"
NEXUS_ROUTER_TRACES_MODULE = "nexus_personal.router_traces"
NEXUS_ROUTER_INTEGRATION = "nexus_personal.router_integration.PersonalRouterIntegration"
NEXUS_ROUTER_SERIALIZERS_MODULE = "nexus_personal.router_serializers"
NEXUS_SOURCE_CANDIDATES_BACKEND = "nexus_personal.source_candidates.PersonalSourceCandidates"
NEXUS_DEPLOYMENT_INTEGRATION = "nexus_personal.deployment_integration.PersonalDeploymentIntegration"
NEXUS_DEPLOYMENT_SERIALIZERS_MODULE = "nexus_personal.deployment_serializers"
NEXUS_DATASET_POLICY_BACKEND = "nexus_personal.dataset_policy.PersonalDatasetPolicy"
NEXUS_DATASET_MODEL_EXTENSION = "nexus_personal.dataset_models"
NEXUS_AGENT_MODEL_EXTENSION = "nexus_personal.agent_models"
NEXUS_AGENT_POLICY_BACKEND = "nexus_personal.agent_policy.PersonalAgentPolicy"
NEXUS_EDGE_POLICY_BACKEND = "nexus_personal.edge_policy.PersonalEdgePolicy"
NEXUS_JOB_ACCESS_BACKEND = "nexus_personal.job_access.PersonalJobAccess"
NEXUS_AGENT_RUNTIME_POLICY_BACKEND = "nexus_personal.agent_runtime_policy.PersonalAgentRuntimePolicy"
NEXUS_AGENT_CONTEXT_EXTENSION_BACKEND = "nexus_personal.agent_runtime_policy.PersonalRunContextExtension"
NEXUS_INVOCATION_LIFECYCLE_BACKEND = "nexus_personal.agent_invocations.PersonalInvocationLifecycle"
NEXUS_AGENT_SERIALIZERS_MODULE = "nexus_personal.agent_serializers"
NEXUS_MOBILE_CONTEXT_BACKEND = "nexus_personal.mobile_policy.PersonalMobileContext"
NEXUS_AUTHORIZATION_BACKEND = "nexus_personal.authorization.PersonalAuthorizationBackend"
NEXUS_PERSONAL_AGENT_LIMITS = {"agents.agents": 100, "agents.mobile_devices": 10, "agents.computers": 10,
    "agents.concurrent_runs": 5, "agents.run_minutes_per_run": 60,
    "agents.runs_per_30_days": 10000, "agents.run_minutes_per_30_days": 60000,
    "agents.hosted_cpu": 2, "agents.hosted_memory_mb": 2048}
NEXUS_MEDIA_POLICY_BACKEND = "nexus_personal.media_policy.PersonalMediaPolicy"
NEXUS_AGENT_ASSET_POLICY_BACKEND = "nexus_personal.agent_asset_policy.PersonalAgentAssetPolicy"
NEXUS_DATASET_SERIALIZERS_MODULE = "nexus_personal.dataset_serializers"
NEXUS_DATASET_VIEWS_MODULE = "nexus_personal.dataset_views"
NEXUS_DATASET_URLCONF = "apps.datasets.core_urls"
