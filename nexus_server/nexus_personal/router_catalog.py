"""Fresh, render-scoped Router graph reads. Never a cross-request auth cache."""
from collections import defaultdict

from rest_framework import exceptions, serializers
from apps.common.resource_facts import ownership_payload
from apps.routers import models
from .resource_catalog import PersonalResourceCatalog
from .router_serializers import RouterSerializer, RouterOutputSerializer, RouterChildBindingSerializer


def fields(serializer, obj):
    # Use existing declared response fields without re-entering the per-object
    # freshness loaders. Only the fresh rows owned by this renderer reach here.
    return serializers.ModelSerializer.to_representation(serializer, obj)


class ChildFields(RouterChildBindingSerializer):
    def __init__(self, snapshot):
        super().__init__()
        self.snapshot = snapshot

    def get_child_output(self, obj):
        value = self.snapshot.output(obj.child_output)
        return None if value is None or value.get('code') else value


class RouterSnapshot:
    def __init__(self, request):
        self.user, self.tenant_id, self.project_id = PersonalResourceCatalog()._context(
            request, getattr(request, 'tenant_id', ''))

    def visible(self, obj, *, active=False):
        return (obj is not None and str(obj.tenant_id) == self.tenant_id
            and (obj.project_id is None or str(obj.project_id) == self.project_id)
            and getattr(obj, 'created_by_id', None) in (None, self.user.pk)
            and (obj.status == 'active' if active else obj.status != 'deleted'))

    def group_visible(self, group):
        return group is None or self.visible(group, active=True)

    def output(self, output):
        if output.status == 'deleted' or not self.visible(output.router):
            return None
        if not self.group_visible(output.model_group):
            return {'id': str(output.pk), 'router_id': str(output.router_id), 'model_name': output.model_name,
                'model_group_id': None, 'model_group_name': None, 'enabled': False,
                'status': 'access_required', 'code': 'MODEL_POOL_UNAVAILABLE'}
        return fields(RouterOutputSerializer(), output)

    def render(self, data):
        positions = list(data.all() if hasattr(data, 'all') else data)
        if any(not isinstance(row, models.Router) for row in positions):
            raise exceptions.NotFound('Router not found.')
        if not positions:
            return []
        ids = [row.pk for row in positions]
        roots = {row.pk: row for row in models.Router.objects.filter(pk__in=ids).select_related('project')}
        if any(pk not in roots or not self.visible(roots[pk]) for pk in ids):
            raise exceptions.NotFound('Router not found.')
        groups, outputs, children = defaultdict(list), defaultdict(list), defaultdict(list)
        for binding in models.RouterModelGroupBinding.objects.filter(router_id__in=ids,
                status='active', enabled=True).select_related('model_group').order_by('priority', 'created_at'):
            if binding.model_group is not None and self.group_visible(binding.model_group):
                groups[binding.router_id].append(str(binding.model_group_id))
        for output in (models.RouterOutput.objects.filter(router_id__in=ids).exclude(status='deleted')
                .select_related('router', 'model_group').order_by('-is_default', 'created_at')):
            payload = self.output(output)
            if payload is not None:
                outputs[output.router_id].append(payload)
        child_fields = ChildFields(self)
        for child in (models.RouterChildBinding.objects.filter(router_id__in=ids).exclude(status='deleted')
                .select_related('child_output', 'child_output__router', 'child_output__model_group')
                .order_by('exposed_model_name', 'priority', 'created_at')):
            payload = fields(child_fields, child)
            if payload['child_output'] is None:
                payload.update(status='access_required', code='EXECUTION_ROUTER_UNAVAILABLE')
            children[child.router_id].append(payload)
        rendered = []
        root_fields = RouterSerializer()
        for pk in ids:
            router = roots[pk]
            payload = fields(root_fields, router)
            payload.update(model_group_ids=groups[pk], outputs=outputs[pk], child_bindings=children[pk])
            rows, key = (children[pk], 'exposed_model_name') if router.router_type == 'aggregation' else (outputs[pk], 'model_name')
            payload['output_models'] = list(dict.fromkeys(row[key] for row in rows
                if row.get('status') == 'active' and row.get('enabled')))
            payload.update(ownership=ownership_payload(project=router.project), access={
                'can_discover': True, 'can_read': True, 'can_use': True, 'can_edit': True,
                'can_manage': True, 'sources': ['personal_owner']})
            rendered.append(payload)
        return rendered


def render_routers(data, request):
    return RouterSnapshot(request).render(data)
