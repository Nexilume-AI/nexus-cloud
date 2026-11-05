"""Optional model-info endpoint; existing OpenAI model directory stays unchanged."""
from rest_framework import exceptions
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView
from django.conf import settings
from django.utils.crypto import salted_hmac

from apps.deployments.candidates import model_group_deployment_candidates
from .model_metadata import LIMITS, EFFORT_FLAGS, aggregate, endpoint_hash, mapping, merge_fields, normalize, reasoning_info
from . import models_dev


def compatible_input_capacity(candidates):
    bounds = []
    for candidate in candidates:
        values = [candidate[key]["value"] for key in ("max_input_tokens", "context_window")
                  if candidate.get(key) and candidate[key]["value"] is not None]
        if not values:
            return None
        bounds.append(min(values))
    return min(bounds) if bounds else None


def router_groups(router, name):
    from .services import child_router_id_invalid
    if router.router_type == router.TYPE_AGGREGATION:
        bindings = router.child_bindings.filter(status="active", enabled=True, exposed_model_name=name).select_related(
            "child_output__router", "child_output__model_group")
        return [binding.child_output.model_group for binding in bindings
                if binding.child_output.status == "active" and binding.child_output.enabled
                and binding.child_output.model_group_id and binding.child_output.router.status == "deployed"
                and not child_router_id_invalid(parent=router, child=binding.child_output.router)]
    outputs = router.outputs.filter(status="active", enabled=True).select_related("model_group")
    if outputs.exists():
        return [output.model_group for output in outputs if output.model_name == name and output.model_group_id]
    return [binding.model_group for binding in router.model_group_bindings.filter(status="active", enabled=True)
            .select_related("model_group") if binding.model_group and binding.model_group.name == name]


def source_reference_identity(source):
    override = getattr(source, "models_dev_mapping", None)
    if override is not None:
        return override
    metadata = mapping(getattr(getattr(source, "runtime_model_offer", None), "metadata", None))
    override = mapping(metadata.get("models_dev"))
    providers = mapping(getattr(settings, "NEXUS_MODELS_DEV_PROVIDER_MAP", {}))
    return {"provider": override.get("provider") or providers.get(source.provider.name, source.provider.name),
            "model": override.get("model") or source.upstream_model_id}


def source_fields(source, catalog, *, include_override=True):
    offer = getattr(source, "runtime_model_offer", None)
    metadata = mapping(getattr(offer, "metadata", None))
    return capability_fields(canonical=source.canonical_model, metadata=metadata,
        runtime=getattr(source, "provider_runtime", None), identity=source_reference_identity(source), catalog=catalog,
        source_override=getattr(source, "model_capabilities_override", None) if include_override else None,
        source_updated_at=getattr(source, "updated_at", None), offer_updated_at=getattr(offer, "updated_at", None))


def provider_fields(offer, runtime, catalog, *, include_override=True):
    account = runtime.provider_account or runtime.source_provider_account
    metadata = dict(mapping(offer.metadata))
    override = mapping(metadata.get("models_dev"))
    provider = account.provider.name
    identity = {"provider": override.get("provider") or mapping(getattr(settings, "NEXUS_MODELS_DEV_PROVIDER_MAP", {})).get(provider, provider),
                "model": override.get("model") or offer.upstream_model_id}
    if not include_override:
        metadata.pop("model_capabilities_override", None)
    return capability_fields(canonical=offer.canonical_model, metadata=metadata, runtime=runtime,
        identity=identity, catalog=catalog, offer_updated_at=offer.updated_at)


def capability_fields(*, canonical, metadata, runtime, identity, catalog, source_override=None,
                      source_updated_at=None, offer_updated_at=None):
    upstream = mapping(metadata.get("model_capacity"))
    # A cached record from an older endpoint must not describe a replacement.
    origin_matches = runtime and upstream.get("endpoint_hash") == endpoint_hash(runtime.internal_api_url)
    canonical_metadata = mapping(getattr(canonical, "metadata", None))
    explicit = mapping(canonical_metadata.get("capacity_override"))
    provider_id, model_id = identity.get("provider"), identity.get("model")
    reference = models_dev.lookup(catalog, provider_id, model_id) if isinstance(provider_id, str) and isinstance(model_id, str) else {}
    reference = {**reference, "reasoning": {"supported": reference.get("reasoning_supported"),
                 "efforts": reference.get("reasoning_efforts"), "included_in_output": reference.get("reasoning_in_output")}}
    administrator = normalize(explicit)
    if administrator["context_window"] is None:
        administrator["context_window"] = normalize({"context_window": getattr(canonical, "context_window", None)})["context_window"]
    reported = normalize(upstream.get("values")) if origin_matches else {}
    def configured(value):
        result = normalize(value)
        # An explicit list supersedes sparse fallback flags too. Do not let a
        # stale upstream flag make clients disagree with the configured list.
        if "reasoning_effort_levels" in mapping(value) or result.get("reasoning_supported") is False:
            result.update({key: key[len("supports_"):-len("_reasoning_effort")] in result.get("reasoning_efforts", []) for key in EFFORT_FLAGS})
        for field in LIMITS:
            if result.get(field) is not None and reported.get(field) is not None:
                result[field] = min(result[field], reported[field])
        return result
    def stamp(date):
        return date.isoformat() if date else None
    return merge_fields([
        (configured(source_override), "source_override", stamp(source_updated_at), False),
        (configured(metadata.get("model_capabilities_override")), "provider_override", stamp(offer_updated_at), False),
        (configured(administrator), "administrator", stamp(getattr(canonical, "updated_at", None)), False),
        (reported, "upstream", upstream.get("updated_at"), True),
        (normalize(reference), "models.dev", catalog.get("updated_at"), False),
    ])


class ModelInfoView(APIView):
    renderer_classes = [JSONRenderer]

    def handle_exception(self, exc):
        if not isinstance(exc, exceptions.APIException):
            return super().handle_exception(exc)
        from .openai_compat import openai_exception_response
        response = openai_exception_response(exc)
        response.data["error"]["param"] = None
        response["Cache-Control"] = "private, no-store"
        return response

    def get(self, request):
        from .integration import gateway_integration
        from apps.common.request_context import get_tenant_from_request
        from .openai_compat import apply_api_key_context
        apply_api_key_context(request)
        # The host supplies authorized groups; never resolve a caller's alias by
        # a global CanonicalModel search or expose physical source configuration.
        resolver = getattr(gateway_integration(), "model_info_targets", None)
        if not callable(resolver):
            raise exceptions.NotFound("Model metadata is unavailable on this host.")
        targets = resolver(request=request)
        tenant = get_tenant_from_request(request)
        # These flags never widen Nexus authorization. LiteLLM team IDs are not
        # Nexus organization IDs; silently accepting one would be misleading.
        if request.query_params.get("teamId"):
            raise exceptions.ValidationError("teamId is unsupported; use the authenticated Nexus scope.")
        for flag in ("include_team_models", "healthy_only"):
            if flag in request.query_params and request.query_params[flag].lower() not in {"true", "false", "1", "0"}:
                raise exceptions.ValidationError({flag: "Expected a boolean."})
        healthy_only = request.query_params.get("healthy_only", "false").lower() in {"true", "1"}
        selected_id = request.query_params.get("litellm_model_id")
        selected = request.query_params.get("model") or request.query_params.get("model_name")
        if selected and selected not in targets:
            raise exceptions.NotFound("Model is unavailable.")
        catalog = models_dev.snapshot()
        rows = []
        for name, groups in targets.items():
            if selected and selected != name:
                continue
            candidates = []
            sources = []
            seen = set()
            for group in groups:
                for source in model_group_deployment_candidates(tenant=tenant, group=group):
                    if source.pk in seen:
                        continue
                    seen.add(source.pk)
                    sources.append(source)
                    candidates.append(source_fields(source, catalog))
            logical_key = ":".join([str(tenant.pk), name, *sorted(str(g.pk) for g in groups)])
            public_id = salted_hmac("nexus.model-info.v1", logical_key, algorithm="sha256").hexdigest()
            if selected_id and selected_id != public_id:
                continue
            if healthy_only and not selected_id and sources and all(s.health_status == "unhealthy" for s in sources):
                continue
            fields = aggregate(candidates)
            info = {key: fields[key]["value"] for key in LIMITS}
            output = info["max_output_tokens"]
            context = info["context_window"]
            # A recommendation is distinct from an advertised hard maximum.
            recommendation = min(output, 32768, context // 2) if output and context else None
            info["recommended_output_tokens"] = recommendation
            info.update(reasoning_info(fields))
            # LiteLLM clients use max_input_tokens as context capacity when no
            # separate input ceiling is known. Keep the raw observation in nexus.
            info["max_input_tokens"] = compatible_input_capacity(candidates)
            from .capabilities import deployment_contract
            contracts = [deployment_contract(source) for source in sources]
            mode = "chat" if contracts and all("chat.completions" in c["operations"] for c in contracts) else None
            info.update(id=public_id, mode=mode, base_model=None,
                        max_tokens=info["max_input_tokens"],
                        input_cost_per_token=None, output_cost_per_token=None)
            rows.append({"model_name": name,
                         "litellm_params": {"model": "openai/" + name, "custom_llm_provider": "openai"},
                         "model_info": info,
                         "nexus": {"schema_version": 1, "fields": fields,
                                   "identity": "logical_gateway_model_not_physical_deployment",
                                   "input_capacity_from_context": fields["max_input_tokens"]["value"] is None and context is not None,
                                   "aggregation": "minimum_across_candidates_unknown_if_any_unknown"}})
        if selected_id and not rows:
            raise exceptions.ValidationError("Model id not found.")
        response = Response({"data": rows})
        response["Cache-Control"] = "private, no-store"
        return response
