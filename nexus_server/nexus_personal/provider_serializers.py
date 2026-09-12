"""Personal Provider operational presentation; no Marketplace or secret output."""
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from django.db.models import Prefetch
from apps.common.resource_catalog import ResourceCatalogListSerializer, resource_context_payload
from apps.common.resource_facts import ownership_payload, provider_connection_project
from apps.providers.models import ProviderRuntimeModelOffer
from apps.providers.connection_inputs import (
    _provider_account_engine, _provider_connection_status, _provider_connection_message, _provider_health_history,
)


class PersonalProviderModelSerializer(serializers.ModelSerializer):
    canonical_model_id = serializers.UUIDField(read_only=True, allow_null=True)
    canonical_model_key = serializers.CharField(source="canonical_model.key", read_only=True, default="")
    source_ids = serializers.SerializerMethodField()

    def get_source_ids(self, obj):
        # The Personal runtime queryset already applies owner/project/status
        # constraints. Do not query an unscoped reverse relation per Offer.
        return [str(source.pk) for source in obj.prefetched_sources]

    class Meta:
        model = ProviderRuntimeModelOffer
        fields = ["id", "canonical_model_id", "canonical_model_key", "upstream_model_id", "status",
                  "health_status", "health_reason", "capabilities", "last_discovered_at", "last_health_check_at",
                  "price_per_1k_tokens", "input_price_per_1k_tokens", "output_price_per_1k_tokens", "image_pricing", "source_ids"]


class PersonalProviderConnectionListSerializer(serializers.ListSerializer):
    def to_representation(self, data):
        from .provider_catalog import PersonalProviderCatalog
        from .provider_runtime_integration import PersonalProviderRuntimeIntegration
        rows = list(data.all() if hasattr(data, "all") else data)
        request = self.context.get("request")
        # This revalidates the current owner/profile/installation once, then
        # reloads every account and runtime. Supplied rows are positions, not
        # authority or a cache of sensitive response fields.
        model, visible = PersonalProviderCatalog()._visible(request=request,
            tenant=getattr(request, "tenant_id", ""))
        if any(not isinstance(row, model) for row in rows):
            raise NotFound("Provider not found.")
        if not rows:
            return []
        runtimes = PersonalProviderRuntimeIntegration()._connection_runtime_queryset(
            str(request.user.pk), request.tenant_id, request.project_id,
            summary=bool(self.context.get("summary")))
        current = {row.pk: row for row in visible.filter(pk__in=[row.pk for row in rows])
            .select_related("provider", "tenant").prefetch_related(Prefetch("source_runtime_accounts",
                queryset=runtimes, to_attr="prefetched_connection_runtimes"))}
        if any(row.pk not in current for row in rows):
            raise NotFound("Provider not found.")
        rendered = []
        for position in rows:
            account = current[position.pk]
            project, repair = provider_connection_project(account)
            runtime = account.prefetched_connection_runtimes[0] if not repair else None
            if (runtime is None or str(runtime.tenant_id) != request.tenant_id
                    or runtime.owner_id not in (None, request.user.pk)
                    or (project is not None and str(project.pk) != request.project_id)):
                raise NotFound("Provider not found.")
            context = {"ownership": ownership_payload(project=project),
                "access": {"can_discover": True, "can_read": True, "can_use": True,
                    "can_edit": True, "can_manage": True, "sources": ["personal_owner"]}}
            rendered.append(self.child._representation(account, context))
        return rendered


class PersonalProviderConnectionSerializer(serializers.Serializer):
    catalog_resource_type = "provider_connection"

    class Meta:
        list_serializer_class = PersonalProviderConnectionListSerializer

    def to_representation(self, account):
        context = resource_context_payload(request=self.context.get("request"), resource_type="provider_connection", obj=account)
        return self._representation(account, context)

    def _representation(self, account, context):
        from apps.providers.connection_services import connection_runtime
        runtime = connection_runtime(account)
        summary = bool(self.context.get("summary"))
        offers = [] if summary or runtime is None else list(runtime.prefetched_model_offers)
        source_count = (runtime.catalog_source_count if summary else len(runtime.prefetched_sources)) if runtime else 0
        actions = ["edit", "remove"]
        if runtime is None:
            actions += ["repair"]
        elif runtime.status in {"created", "stopped", "failed"}:
            actions += ["start"]
        elif runtime.status in {"login_required", "unhealthy", "active"}:
            actions += ["stop", "health", "refresh_models"]
            if runtime.status == "login_required":
                actions += ["login"]
        return {"id": str(account.pk), "name": account.name or account.account_id,
            "account_identity": account.account_id, "upstream_provider": account.provider.name,
            "url": account.url, "engine": runtime.runtime_type if runtime else _provider_account_engine(account),
            "auth_mode": account.auth_mode, "status": _provider_connection_status(account=account, runtime=runtime),
            "status_message": _provider_connection_message(account=account, runtime=runtime),
            "login_status": account.login_status, "last_error": (runtime.last_error if runtime else "") or account.last_login_error,
            "models": PersonalProviderModelSerializer(offers, many=True).data,
            "model_count": runtime.catalog_model_count if runtime and summary else len(offers), "models_loaded": not summary,
            "source_count": source_count, "available_actions": actions,
            "technical_details": {"runtime_id": str(runtime.pk) if runtime else "",
                "runtime_name": runtime.name if runtime else "", "runtime_status": runtime.status if runtime else "missing",
                "runtime_type": runtime.runtime_type if runtime else ""},
            "ownership": context["ownership"], "access": context["access"],
            "quota": {"status": account.quota_status, "remaining_tokens": account.quota_remaining_tokens,
                "remaining_requests": account.quota_remaining_requests, "reset_at": account.quota_reset_at},
            "last_checked_at": runtime.last_health_check_at if runtime else account.last_checked_at,
            "health_history": [] if summary else _provider_health_history(runtime),
            "created_at": account.created_at, "updated_at": max(account.updated_at, runtime.updated_at) if runtime else account.updated_at}
