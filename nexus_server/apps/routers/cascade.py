"""Bounded, tenant-local Router DAG. Resolution never invokes another Gateway."""
from rest_framework import exceptions
from .models import Router, RouterChildBinding, RouterOutput

MAX_ROUTER_DEPTH = 4  # Includes the final Execution Router.


def active_bindings(router):
    return router.child_bindings.filter(status="active", enabled=True).select_related(
        "child_output__router", "child_output__model_group").order_by("priority", "created_at")


def validate_graph(tenant_id, *, edge=None):
    """Check the whole tenant so extending a subtree also checks its ancestors.

    Mutating callers must hold the Tenant row lock, serializing opposing edges.
    Runtime walkers independently guard their paths against legacy/bypassed writes.
    """
    graph = {}
    for parent, child, child_tenant in RouterChildBinding.objects.filter(
            router__tenant_id=tenant_id, status="active", enabled=True
    ).exclude(router__status="deleted").values_list(
            "router_id", "child_output__router_id", "child_output__router__tenant_id"):
        if child_tenant != tenant_id:
            raise exceptions.ValidationError("Router cascades must stay in one Organization.")
        graph.setdefault(parent, set()).add(child)
    if edge:
        graph.setdefault(edge[0], set()).add(edge[1])
    heights = {}

    def height(node, path):
        if node in path:
            raise exceptions.ValidationError("Router cascade would create a cycle.")
        if len(path) >= MAX_ROUTER_DEPTH:
            raise exceptions.ValidationError(f"Router cascades allow at most {MAX_ROUTER_DEPTH} Router levels, including execution.")
        if node in heights:
            result = heights[node]
        else:
            result = 1 + max((height(child, path | {node}) for child in graph.get(node, ())), default=0)
            heights[node] = result
        if len(path) + result > MAX_ROUTER_DEPTH:
            raise exceptions.ValidationError(f"Router cascades allow at most {MAX_ROUTER_DEPTH} Router levels, including execution.")
        return result
    for node in graph:
        height(node, set())


def sync_outputs(router):
    """Stable exports make aggregate aliases selectable by another Router."""
    if router.router_type != Router.TYPE_AGGREGATION:
        return
    from .aggregate_auto import enabled
    names = list(dict.fromkeys(active_bindings(router).values_list("exposed_model_name", flat=True)))
    if enabled(router):
        names.append("auto")
    router.outputs.exclude(model_name__in=names).update(enabled=False, is_default=False)
    for index, name in enumerate(names):
        RouterOutput.objects.update_or_create(router=router, model_name=name, defaults={
            "model_group": None, "enabled": True, "status": "active", "deleted_at": None,
            "is_default": index == 0,
        })


def output_groups(output, *, path=(), allow_auto=True):
    """Metadata/availability walker with the same depth and decision boundaries."""
    router = output.router
    if router.pk in path or len(path) >= MAX_ROUTER_DEPTH or router.status != "deployed":
        return []
    if output.status != "active" or not output.enabled:
        return []
    from .decision_routing import is_auto_output, output_groups as execution_groups
    if router.router_type == Router.TYPE_EXECUTION:
        return [] if is_auto_output(output) and not allow_auto else execution_groups(output)
    from .aggregate_auto import enabled
    auto = output.model_name == "auto" and enabled(router)
    if auto and not allow_auto:
        return []
    bindings = active_bindings(router)
    if not auto:
        bindings = bindings.filter(exposed_model_name=output.model_name)
    groups = {}
    for binding in bindings:
        child = binding.child_output
        if child.router.tenant_id != router.tenant_id:
            continue
        for group in output_groups(child, path=(*path, router.pk), allow_auto=allow_auto and not auto):
            groups[group.pk] = group
    return list(groups.values())
