"""Personal Router management over actual owner-scoped Pool/Source records."""
from types import SimpleNamespace
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Q
from rest_framework import exceptions
from apps.routers.models import Router
from .resource_catalog import PersonalResourceCatalog
from .deployment_integration import PersonalDeploymentIntegration
from .services import installation_context


class PersonalRouterIntegration:
    def log_runtime_invocation(self, **kwargs):
        from .router_runtime import log_runtime_invocation
        return log_runtime_invocation(**kwargs)

    def _request(self, user):
        if not isinstance(user, get_user_model()):
            raise exceptions.AuthenticationFailed("Sign in as the personal instance owner.")
        return SimpleNamespace(user=user, headers={}, META={}, query_params={})

    def visible_routers(self, *, user, tenant):
        return PersonalResourceCatalog().discoverable_resource_queryset(Router.objects.all(),
            request=self._request(user), tenant=tenant, resource_type="router").select_related("project")

    def _authorized(self, user, router):
        try:
            # Re-read ownership: stale objects and API principals cannot grant access.
            PersonalResourceCatalog()._resource(request=self._request(user), resource_type="router", obj=router)
        except exceptions.APIException:
            return False
        return True

    def can_manage_router(self, *, user, router):
        return self._authorized(user, router)

    def can_use_router(self, *, user, router):
        return self._authorized(user, router)

    def candidate_outputs(self, *, request, queryset):
        _, tenant_id, _ = installation_context()
        groups = PersonalDeploymentIntegration().visible_model_groups(user=request.user, tenant=tenant_id)
        routers = self.visible_routers(user=request.user, tenant=tenant_id)
        return queryset.filter(router__in=routers).filter(Q(model_group__isnull=True) | Q(model_group__in=groups))

    def validate_model_groups(self, *, tenant, model_group_ids):
        owner_id, _, _ = installation_context()
        owner = get_user_model().objects.get(pk=owner_id)
        policy = PersonalDeploymentIntegration()
        request = self._request(owner)
        requested = {str(value) for value in model_group_ids}
        groups = list(policy.visible_model_groups(user=owner, tenant=tenant).filter(pk__in=requested))
        if requested != {str(group.pk) for group in groups}:
            raise exceptions.ValidationError("Select a Model Pool owned by this personal instance.")
        for group in groups:
            links = list(group.deployment_links.exclude(status="deleted").select_related("deployment"))
            policy.validate_routing_preview(request=request, group=group, links=links)

    def validate_pool_bindings(self, *, request, router, providers, model_group_ids):
        if providers:
            raise exceptions.ValidationError("Select a real Model Pool; legacy Provider-name bindings are not supported.")
        self.validate_model_groups(tenant=router.tenant, model_group_ids=model_group_ids or [])

    def validate_deployment(self, *, request, router):
        group_ids = list(router.model_group_bindings.filter(status="active", enabled=True)
                         .values_list("model_group_id", flat=True))
        if any(value is None for value in group_ids):
            raise exceptions.ValidationError("Reconnect legacy bindings to a real Model Pool before deploying.")
        self.validate_model_groups(tenant=router.tenant, model_group_ids=group_ids)
        for binding in router.child_bindings.filter(status="active", enabled=True).select_related("child_output__router"):
            if not self.can_use_router(user=request.user, router=binding.child_output.router):
                raise exceptions.NotFound("Execution Router is no longer available.")
            if binding.child_output.model_group_id is None:
                raise exceptions.ValidationError("Reconnect the Execution output to a real Model Pool.")
            self.validate_model_groups(tenant=router.tenant, model_group_ids=[binding.child_output.model_group_id])

    def set_pricing(self, *, request, router_id, plan_id, pricing_json=None):
        from apps.routers.services import get_mutable_router
        get_mutable_router(request=request, router_id=router_id)
        raise exceptions.ValidationError("Router pricing is not part of the personal distribution.")

    def export_router_credentials(self, *, request, router_id):
        from .router_credentials import export_router_credentials
        return export_router_credentials(request=request, router_id=router_id)
