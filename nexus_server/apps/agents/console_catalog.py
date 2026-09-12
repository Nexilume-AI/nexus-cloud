"""Database filters for the private Agent directory (before pagination)."""
from django.db.models import Case, Exists, IntegerField, OuterRef, Q, Subquery, Value, When
from django.utils import timezone
from django.db.models.functions import Coalesce
from rest_framework.exceptions import ValidationError

from .models import AgentRuntimeDeployment, AgentRuntimeImage


def filter_catalog(queryset, request):
    params = request.query_params
    query = (params.get("q") or "").strip()
    if len(query) > 200:
        raise ValidationError({"q": "Search is limited to 200 characters."})
    if query:
        query_filter = (Q(name__icontains=query) | Q(status__icontains=query)
            | Q(current_image__image_ref__icontains=query) | Q(current_image__version__version__icontains=query)
            | Q(project__name__icontains=query))
        if query.lower() in "organization shared":
            query_filter |= Q(project__isnull=True)
        queryset = queryset.filter(query_filter)
    ownership = params.get("ownership", "all")
    if ownership not in {"all", "organization", "project"}:
        raise ValidationError({"ownership": "Select organization or project."})
    if ownership != "all":
        queryset = queryset.filter(project__isnull=ownership == "organization")
    lifecycle = params.get("lifecycle", "all")
    if lifecycle not in {"all", "active", "draft", "archived"}:
        raise ValidationError({"lifecycle": "Unknown lifecycle filter."})
    if lifecycle == "archived":
        queryset = queryset.filter(status="archived")
    elif lifecycle != "all":
        now = timezone.now()
        # Mirror latest_runtime_deployment: prefer effectively active runtimes,
        # otherwise the most recently updated non-deleted runtime.
        edge_available = Q(edge_registration__status="active", edge_registration__lease_expires_at__gt=now) & (
            Q(edge_registration__node__presence_protocol_version__lt=1) | (
                Q(edge_registration__node__last_presence_at__isnull=False,
                  edge_registration__node__presence_expires_at__gt=now,
                  edge_registration__node__connection_status__in=["online", "degraded"])
                & ~Q(edge_registration__node__status="deleted")))
        running = ~Q(runtime_kind="docker") | Q(docker_lifecycle__desired__isnull=True) | ~Q(docker_lifecycle__desired="stopped")
        active = Q(status="active") & (Q(edge_registration__isnull=True) | edge_available) & running
        runtimes = AgentRuntimeDeployment.objects.filter(agent_id=OuterRef("pk")).exclude(status="deleted").annotate(
            catalog_active=Case(When(active, then=Value(1)), default=Value(0), output_field=IntegerField())
        ).order_by("-catalog_active", "-updated_at")
        queryset = queryset.annotate(
            catalog_runtime_kind=Coalesce(Subquery(runtimes.values("runtime_kind")[:1]), Value("")),
            catalog_edge_id=Subquery(runtimes.values("edge_registration_id")[:1]),
            catalog_has_image=Exists(AgentRuntimeImage.objects.filter(agent_id=OuterRef("pk")).exclude(status="deleted")),
        )
        edge = Q(catalog_runtime_kind__in=["openwrt_ipv6", "openwrt_relay"])
        configured = (edge & Q(catalog_edge_id__isnull=False)) | (~edge & Q(catalog_has_image=True))
        queryset = queryset.exclude(status__in=["archived", "disabled"])
        queryset = queryset.filter(configured) if lifecycle == "active" else queryset.exclude(configured)
    order = {"updated": "-updated_at", "created": "-created_at", "name": "name"}.get(params.get("sort", "updated"))
    if order is None:
        raise ValidationError({"sort": "Unknown Agent ordering."})
    return queryset, order
