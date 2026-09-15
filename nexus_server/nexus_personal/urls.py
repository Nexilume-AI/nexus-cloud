"""Personal HTTP composition, shared by the product host and isolation tests.

Provider/Router/hosted deployment composition is still in progress. This URL
inventory is not an assertion that an independently installable release exists.
Do not import Enterprise URLs, public signup, tenancy management or test views.
"""
from django.urls import include, path
from apps.accounts.views import (
    AccountMeView, ChangePasswordView, LoginView, LogoutView, WhoAmIView,
)
from .http import PersonalBootstrapView, PersonalContextView
from .credential_views import RouterCredentialListView, RouterCredentialRevokeView
from apps.workspaces.connection_views import WorkspaceConnectionDetailView, WorkspaceConnectionListView, WorkspaceConnectionTestView
from .tool_recovery_views import ToolRecoveryView
from .provider_recovery_views import PersonalLegacyProviderListView, PersonalLegacyProviderDetailView
from apps.agents.run_capacity import PrivateRunCapacityRecoveryView


urlpatterns = [
    path("api/v1/agents/<uuid:agent_id>/private-runs/capacity-recovery/", PrivateRunCapacityRecoveryView.as_view(), name="private-run-capacity-recovery"),
    path("api/v1/provider-runtimes/", PersonalLegacyProviderListView.as_view(), name="personal-provider-recovery"),
    path("api/v1/provider-runtimes/<uuid:runtime_id>/", PersonalLegacyProviderDetailView.as_view(), name="personal-provider-recovery-detail"),
    path("api/v1/personal/context/", PersonalContextView.as_view(), name="personal-context"),
    path("api/v1/workspace-terminal-sessions/<uuid:session_id>/tool-config/recovery/", ToolRecoveryView.as_view(), name="personal-tool-recovery"),
    path("api/v1/workspace-connections/", WorkspaceConnectionListView.as_view(), name="workspace-connection-list"),
    path("api/v1/workspace-connections/<uuid:connection_id>/", WorkspaceConnectionDetailView.as_view(), name="workspace-connection-detail"),
    path("api/v1/workspace-connections/<uuid:connection_id>/test/", WorkspaceConnectionTestView.as_view(), name="workspace-connection-test"),
    path("api/v1/routers/<uuid:router_id>/credentials/", RouterCredentialListView.as_view(), name="personal-router-credentials"),
    path("api/v1/routers/<uuid:router_id>/credentials/<uuid:credential_id>/revoke/", RouterCredentialRevokeView.as_view(), name="personal-router-credential-revoke"),
    path("api/v1/public/bootstrap/", PersonalBootstrapView.as_view(), name="public-bootstrap"),
    path("api/v1/auth/login/", LoginView.as_view(), name="login"),
    path("api/v1/auth/logout/", LogoutView.as_view(), name="logout"),
    path("api/v1/auth/whoami/", WhoAmIView.as_view(), name="whoami"),
    path("api/v1/account/me/", AccountMeView.as_view(), name="account-me"),
    path("api/v1/account/change-password/", ChangePasswordView.as_view(), name="account-change-password"),
    path("api/v1/", include("apps.datasets.urls")),
    path("api/v1/", include("apps.providers.import_urls")),
    path("api/v1/", include("apps.providers.connection_urls")),
    path("api/v1/", include("apps.deployments.source_urls")),
    path("api/v1/", include("apps.routers.management_urls")),
    path("api/v1/", include("apps.gateway.chat_urls")),
    path("api/v1/", include("apps.gateway.image_urls")),
    path("api/v1/", include("apps.mobile.urls")),
    path("api/v1/", include("apps.workspaces.computer_urls")),
    path("api/v1/", include("apps.workspaces.terminal_urls")),
    path("api/v1/", include("apps.workspaces.tool_urls")),
    path("api/v1/", include("apps.agents.catalog_urls")),
    path("api/v1/", include("apps.agents.configuration_urls")),
    path("api/v1/", include("apps.agents.device_urls")),
    path("api/v1/", include("apps.agents.operations_urls")),
    path("api/v1/", include("apps.agents.hosted_urls")),
    path("api/v1/", include("apps.jobs.urls")),
    path("api/v1/", include("apps.metrics.urls")),
    path("api/v1/", include("apps.agents.edge_urls")),
    path("api/v1/", include("apps.agents.mcp_urls")),
    path("api/v1/", include("apps.agents.file_urls")),
    path("api/v1/", include("apps.agents.private_display_urls")),
    path("api/v1/", include("apps.agents.conversation_urls")),
    path("api/v1/", include("apps.agents.callback_urls")),
    path("api/v1/", include("apps.agents.callback_event_urls")),
    path("api/v1/", include("apps.agents.observability_urls")),
    path("api/v1/", include("apps.notifications.inbox_urls")),
]
