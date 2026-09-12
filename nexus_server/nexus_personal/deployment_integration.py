"""Personal Sources use the installed owner's actual Provider and Pool records."""
from types import SimpleNamespace
from django.db.models import Q
from rest_framework import exceptions
from apps.deployments.models import Deployment, ModelGroup
from apps.providers.models import ProviderRuntimeAccount
from .resource_catalog import PersonalResourceCatalog
from .services import installation_context


class PersonalDeploymentIntegration:
    def topology(self):
        from .deployment_topology import PersonalDeploymentTopology
        return PersonalDeploymentTopology()

    def _request(self, user):
        return SimpleNamespace(user=user, META={}, headers={}, query_params={})

    def _visible(self, *, request, tenant, model, resource_type):
        return PersonalResourceCatalog().discoverable_resource_queryset(model.objects.all(),
            request=request, tenant=tenant, resource_type=resource_type)

    def visible_deployments(self, *, user, tenant):
        request = self._request(user)
        user, tenant_id, project_id = PersonalResourceCatalog()._context(request, tenant)
        scoped = self._visible(request=request, tenant=tenant, model=Deployment, resource_type="deployment")
        for field, owner_field in (("provider_account", "created_by"), ("provider_runtime", "owner"),
                                   ("provider_runtime__provider_account", "created_by"),
                                   ("provider_runtime__source_provider_account", "created_by")):
            scoped = scoped.filter(Q(**{field + "__isnull": True}) | (
                Q(**{field + "__tenant_id": tenant_id}) & (
                    Q(**{field + "__" + owner_field + "_id": user.pk}) |
                    Q(**{field + "__" + owner_field + "__isnull": True}))))
        scoped = scoped.filter(Q(provider_runtime__isnull=True) | Q(provider_runtime__project_id=project_id) |
                               Q(provider_runtime__project__isnull=True))
        return scoped.select_related("provider", "provider_account", "canonical_model",
                                     "provider_runtime", "runtime_model_offer", "project", "team")

    def visible_model_groups(self, *, user, tenant):
        return self._visible(request=self._request(user), tenant=tenant, model=ModelGroup,
            resource_type="model_group").filter(status="active")

    def resolve_request_project(self, *, request, tenant):
        return PersonalResourceCatalog().resolve_ownership_project(request=request, tenant=tenant, ownership=None)[0]

    def scope_queryset_to_request_ownership(self, *, queryset, request):
        _, tenant_id, _ = installation_context()
        user, _, project_id = PersonalResourceCatalog()._context(request, tenant_id)
        if queryset.model in (Deployment, ModelGroup):
            kind = "deployment" if queryset.model is Deployment else "model_group"
            scoped = PersonalResourceCatalog().discoverable_resource_queryset(queryset, request=request,
                tenant=tenant_id, resource_type=kind)
            return scoped.filter(project_id=project_id)
        if queryset.model is ProviderRuntimeAccount:
            scoped = queryset.filter(tenant_id=tenant_id, project_id=project_id).filter(
                Q(owner_id=user.pk) | Q(owner__isnull=True))
            # Even a locally owned Runtime cannot import another owner's credential.
            for field in ("source_provider_account", "provider_account"):
                scoped = scoped.filter(Q(**{field + "__isnull": True}) | (
                    Q(**{field + "__tenant_id": tenant_id}) & (
                        Q(**{field + "__created_by_id": user.pk}) | Q(**{field + "__created_by__isnull": True}))))
            return scoped
        raise exceptions.ValidationError("This resource has no personal Source mutation scope.")

    def validate_model_group(self, *, request, group):
        current, _ = PersonalResourceCatalog()._resource(request=request, resource_type="model_group", obj=group)
        if current.status != "active":
            raise exceptions.NotFound("Model Pool not found.")

    def validate_restoration(self, *, tenant, deployment_id, defaults):
        request = self._request(defaults.get("created_by"))
        _, tenant_id, project_id = PersonalResourceCatalog()._context(request, tenant)
        project = defaults.get("project")
        if str(getattr(project, "pk", "")) != project_id:
            raise exceptions.NotFound("Source not found.")
        existing = Deployment.objects.select_for_update().filter(tenant_id=tenant_id, deployment_id=deployment_id).first()
        if existing and (str(existing.project_id or "") != project_id or
                         (existing.created_by_id is not None and existing.created_by_id != request.user.pk)):
            raise exceptions.NotFound("Source not found.")

    def remove_contributions(self, *, deployment, now):
        # No commercial tables are installed. Source/link cleanup is still done
        # by the original shared transaction; there is no purchase to fabricate.
        return None

    def validate_routing_preview(self, *, request, group, links):
        self.validate_model_group(request=request, group=group)
        source_ids = {link.deployment_id for link in links}
        visible_ids = set(self.visible_deployments(user=request.user, tenant=group.tenant_id)
                          .filter(pk__in=source_ids).values_list("pk", flat=True))
        if source_ids != visible_ids or any(
            link.deployment.tenant_id != group.tenant_id or
            link.deployment.project_id != group.project_id or
            link.deployment.canonical_model_id != group.canonical_model_id for link in links
        ):
            raise exceptions.NotFound("A Pool Source is no longer available. Reload the Pool.")

    def affected_router_ids(self, *, request, group):
        from apps.routers.models import Router, RouterModelGroupBinding, RouterOutput
        self.validate_model_group(request=request, group=group)
        routers = self._visible(request=request, tenant=group.tenant_id, model=Router, resource_type="router")
        # Count actual direct consumers, once even if both a binding and output
        # reference the Pool. Do not invent zero impact or expose another owner.
        router_ids = set(RouterModelGroupBinding.objects.filter(model_group=group, router__in=routers)
                         .exclude(status="deleted").values_list("router_id", flat=True))
        router_ids.update(RouterOutput.objects.filter(model_group=group, router__in=routers)
                          .exclude(status="deleted").values_list("router_id", flat=True))
        return router_ids

    def _unsupported_origin(self):
        raise exceptions.ValidationError({"source_type": "Personal Sources require a discovered local Provider Runtime."},
                                         code="SOURCE_ORIGIN_UNAVAILABLE")

    def create_additional_source(self, *, request, tenant, data):
        self._unsupported_origin()

    def create_deployment_from_marketplace(self, *, request, tenant, data):
        self._unsupported_origin()

    def resolve_marketplace_model_group(self, *, request, tenant, data, canonical_model):
        self._unsupported_origin()

    def marketplace_source_id(self, *, contribution):
        self._unsupported_origin()

    def batch_source_data(self, *, runtime_id, item, source_data):
        self._unsupported_origin()
