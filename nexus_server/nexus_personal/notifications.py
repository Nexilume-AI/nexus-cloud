"""Single-owner Inbox policy, with no role audience or commercial sources."""
from django.db.models import Q
from django.core.exceptions import ImproperlyConfigured
from rest_framework import exceptions
from apps.common.request_context import get_tenant_from_request
from apps.notifications.models import InboxItem
from .authentication import validate_owner
from .services import installation_context


class PersonalNotificationBackend:
    # Core Agent, Job and Data signals are installed by the shared AppConfig.
    # Operational maintenance uses the actual resource models, not role queues.
    additional_signal_modules = ()
    source_types = ("run", "agent_recovery", "build", "job", "dataset_import",
                    "computer_issue", "mobile_issue", "edge_router_issue", "agent_runtime_issue", "provider_runtime_issue")

    def maintenance_queryset(self, *, name, queryset):
        owner, tenant, project = installation_context()
        if name in {"active-items", "expired-items"}:
            return queryset.filter(tenant_id=tenant, recipient_id=owner,
                audience_type=InboxItem.AUDIENCE_PERSONAL, required_permission="",
                source_type__in=self.source_types).filter(Q(project_id=project) | Q(project__isnull=True))
        if name in {"interactions", "runs", "recovery"}:
            prefix = "" if name == "runs" else "run__"
            return queryset.filter(**{
                prefix + "consumer_tenant_id": tenant, prefix + "caller_principal_id": owner,
                prefix + "caller_principal_type": "user", prefix + "tenant_id": tenant,
            }).filter(Q(**{prefix + "consumer_project_id": project}) | Q(**{prefix + "consumer_project__isnull": True}))
        # Explicit owner paths: adding a new maintenance source requires review.
        owners = {"builds": "created_by_id", "jobs": "created_by_id", "imports": "requested_by_id",
            "nodes": "registered_by_id", "agent-runtimes": "agent__created_by_id", "provider-runtimes": "owner_id",
            "computers": "created_by_id", "mobiles": "created_by_id"}
        if name not in owners:
            raise ImproperlyConfigured("Unknown personal Inbox maintenance source.")
        project_path = {"imports": "dataset__project", "builds": "agent__project"}.get(name, "project")
        tenant_path = "agent__tenant_id" if name == "builds" else "tenant_id"
        query = queryset.filter(**{tenant_path: tenant}).filter(
            Q(**{project_path + "_id": project}) | Q(**{project_path + "__isnull": True}))
        if name == "imports":
            query = query.filter(dataset__tenant_id=tenant, context_project_id__in=["", project])
        owner_path = owners[name]
        # Operational resources without an explicit creator belong to this
        # installation. Personal devices and user work must have the owner.
        owner_filter = Q(**{owner_path: owner})
        if name in {"agent-runtimes", "provider-runtimes"}:
            owner_filter |= Q(**{owner_path + "__isnull": True})
        return query.filter(owner_filter)

    def record_operational_issue(self, **kwargs):
        from apps.notifications.sources import record_operational_issue
        owner, _, _ = installation_context()
        source = kwargs["source"]
        names = {"edge_router_issue": "nodes", "agent_runtime_issue": "agent-runtimes",
            "provider_runtime_issue": "provider-runtimes", "computer_issue": "computers", "mobile_issue": "mobiles"}
        name = names.get(kwargs["kind"])
        if name is None:
            raise ImproperlyConfigured("Unknown personal operational notification.")
        if not self.maintenance_queryset(name=name, queryset=type(source).objects.filter(pk=source.pk)).exists():
            return None
        return record_operational_issue(**{**kwargs, "recipient_id": owner, "permission": ""})

    def reconcile_extra_sources(self, *, batch):
        # No additional products in this host. Core repair is never skipped;
        # commercial metrics and workflow tables are absent from its schema.
        return None

    def authorized_project_ids(self, *, request, tenant):
        validate_owner(request, request.user)
        _, tenant_id, project_id = installation_context()
        if str(tenant.pk) != tenant_id:
            raise exceptions.NotFound("Inbox is unavailable.")
        return {project_id}

    def visible_items(self, *, request):
        from apps.notifications.inbox import require_personal_user
        require_personal_user(request)
        tenant = get_tenant_from_request(request)
        projects = self.authorized_project_ids(request=request, tenant=tenant)
        return InboxItem.objects.filter(tenant=tenant, recipient=request.user,
            audience_type=InboxItem.AUDIENCE_PERSONAL, required_permission="",
            source_type__in=self.source_types).filter(Q(project__isnull=True) | Q(project_id__in=projects))

    def user_can_receive_role_item(self, *, item, user):
        return False

    def role_subscriber_ids(self, *, item):
        return []

    def refresh_external_item(self, *, item, state, resolved_at):
        if item.source_type not in self.source_types:
            raise exceptions.NotFound("Inbox source is unavailable.")
        return state, resolved_at

    def navigation_routes(self, *, item):
        return {}

    def export_source(self, name):
        raise AttributeError(name)

    def query_sources(self):
        from apps.providers.models import ProviderRuntimeAccount
        return [("provider_runtime_issue", ProviderRuntimeAccount, "status",
                 {"failed": "needs_action", "unhealthy": "needs_action"})]
