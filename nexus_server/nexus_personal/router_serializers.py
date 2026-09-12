"""Personal Router presentation: fresh ownership, no commercial schema."""
from rest_framework import exceptions, serializers
from apps.common.resource_catalog import resource_context_payload
from apps.routers import inputs, models
from .deployment_integration import PersonalDeploymentIntegration
from .resource_catalog import PersonalResourceCatalog


def response_context(request):
    return {"context": {"request": request}}


class PersonalInput:
    def to_internal_value(self, data):
        if isinstance(data, dict) and set(data) - set(self.fields):
            raise serializers.ValidationError({"fields": "Unsupported personal Router fields."})
        return super().to_internal_value(data)


class RouterCreateSerializer(PersonalInput, inputs.RouterCreateSerializer):
    pass


class RouterUpdateSerializer(PersonalInput, inputs.RouterUpdateSerializer):
    pass


class RouterOutputUpdateSerializer(PersonalInput, inputs.RouterOutputUpdateSerializer):
    pass


class RouterChildBindingCreateSerializer(PersonalInput, inputs.RouterChildBindingCreateSerializer):
    pass


class RouterChildBindingUpdateSerializer(PersonalInput, inputs.RouterChildBindingUpdateSerializer):
    pass


class RouterModelGroupBindSerializer(PersonalInput, inputs.RouterModelGroupBindSerializer):
    pass


class RouterPolicySerializer(PersonalInput, inputs.RouterPolicySerializer):
    pass


def current_router(request, instance):
    return PersonalResourceCatalog()._resource(request=request, resource_type="router", obj=instance)[0]


def group_visible(request, group):
    if group is None:
        return True
    try:
        PersonalDeploymentIntegration().validate_model_group(request=request, group=group)
    except exceptions.APIException:
        return False
    return True


class RouterRelatedSerializer(serializers.ModelSerializer):
    def current(self, instance):
        current = self.Meta.model.objects.filter(pk=instance.pk).exclude(status="deleted").select_related("router").first()
        if current is None:
            raise exceptions.NotFound("Router record not found.")
        current_router(self.context.get("request"), current.router)
        return current

    def to_representation(self, instance):
        return super().to_representation(self.current(instance))


class RouterOutputSerializer(RouterRelatedSerializer):
    router_id = serializers.UUIDField(read_only=True)
    router_name = serializers.CharField(source="router.name", read_only=True)
    model_group_id = serializers.UUIDField(read_only=True, allow_null=True)
    model_group_name = serializers.CharField(source="model_group.name", read_only=True, allow_null=True)

    class Meta:
        model = models.RouterOutput
        fields = ["id", "router_id", "router_name", "model_name", "model_group_id", "model_group_name",
                  "description", "enabled", "is_default", "status", "created_at", "updated_at"]

    def to_representation(self, instance):
        current = self.current(instance)
        if not group_visible(self.context.get("request"), current.model_group):
            return {"id": str(current.pk), "router_id": str(current.router_id), "model_name": current.model_name,
                    "model_group_id": None, "model_group_name": None, "enabled": False,
                    "status": "access_required", "code": "MODEL_POOL_UNAVAILABLE"}
        return serializers.ModelSerializer.to_representation(self, current)


class RouterChildBindingSerializer(RouterRelatedSerializer):
    router_id = serializers.UUIDField(read_only=True)
    child_output = serializers.SerializerMethodField()

    class Meta:
        model = models.RouterChildBinding
        fields = ["id", "router_id", "exposed_model_name", "child_output", "enabled", "priority",
                  "weight", "status", "created_at", "updated_at"]

    def get_child_output(self, obj):
        try:
            payload = RouterOutputSerializer(obj.child_output, context=self.context).data
        except exceptions.APIException:
            return None
        return None if payload.get("code") else payload

    def to_representation(self, instance):
        payload = super().to_representation(instance)
        if payload["child_output"] is None:
            payload.update(status="access_required", code="EXECUTION_ROUTER_UNAVAILABLE")
        return payload


class RouterVersionSerializer(RouterRelatedSerializer):
    class Meta:
        model = models.RouterVersion
        fields = ["id", "version", "file_name", "file_size", "status", "created_at", "updated_at"]


class RouterDeploymentSerializer(RouterRelatedSerializer):
    router_id = serializers.UUIDField(read_only=True)
    version = serializers.CharField(source="version.version", read_only=True, allow_null=True)

    class Meta:
        model = models.RouterDeployment
        fields = ["id", "router_id", "version", "status", "endpoint_url", "created_at", "updated_at"]


class RouterModelGroupBindingSerializer(RouterRelatedSerializer):
    model_group_id = serializers.UUIDField(read_only=True, allow_null=True)
    model_group_display_name = serializers.CharField(source="model_group.display_name", read_only=True, allow_null=True)

    class Meta:
        model = models.RouterModelGroupBinding
        fields = ["id", "model_group_id", "model_group_display_name", "provider_name", "model_group_name",
                  "enabled", "priority", "weight", "routing_hint", "status", "created_at", "updated_at"]

    def to_representation(self, instance):
        current = self.current(instance)
        if not group_visible(self.context.get("request"), current.model_group):
            return {"id": str(current.pk), "model_group_id": None, "status": "access_required", "code": "MODEL_POOL_UNAVAILABLE"}
        return serializers.ModelSerializer.to_representation(self, current)


class RouterListSerializer(serializers.ListSerializer):
    def to_representation(self, data):
        from .router_catalog import render_routers
        return render_routers(data, self.context.get('request'))


class RouterSerializer(serializers.ModelSerializer):
    tenant_id = serializers.UUIDField(read_only=True)
    project_id = serializers.UUIDField(read_only=True, allow_null=True)

    class Meta:
        model = models.Router
        list_serializer_class = RouterListSerializer
        fields = ["id", "tenant_id", "project_id", "name", "strategy", "status", "current_version",
                  "router_type", "created_at", "updated_at"]

    def to_representation(self, instance):
        from .router_catalog import render_routers
        return render_routers([instance], self.context.get('request'))[0]
