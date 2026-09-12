"""Bounded, scope-filtered monitoring lists. Legacy small arrays stay complete."""
from django.db.models import Q
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from apps.common.catalog_pagination import page, requested


def respond(request, queryset, serializer, *, search_fields=(), serialize=None):
    query = request.query_params.get("q", "").strip()
    if len(query) > 200:
        raise ValidationError({"q": "Search is limited to 200 characters."})
    if query and search_fields:
        condition = Q()
        for field in search_fields:
            condition |= Q(**{field + "__icontains": query})
        queryset = queryset.filter(condition)
    from django.utils.dateparse import parse_datetime
    from django.utils import timezone
    for parameter, lookup in (("created_from", "created_at__gte"), ("created_to", "created_at__lt")):
        value = request.query_params.get(parameter)
        if value:
            parsed = parse_datetime(value)
            if not parsed or timezone.is_naive(parsed):
                raise ValidationError({parameter: "Use an ISO timestamp with timezone."})
            queryset = queryset.filter(**{lookup: parsed})
    state = request.query_params.get("status")
    if state and state != "all":
        field = "delivery_status" if "delivery_status" in {f.name for f in queryset.model._meta.fields} else "status"
        queryset = queryset.filter(**{field: state})
    def default_serialize(rows):
        return serializer(rows, many=True, context={"request": request}).data
    serialize = serialize or default_serialize
    if requested(request):
        data = page(request=request, queryset=queryset, serialize=serialize)
    else:
        rows = list(queryset[:101])
        if len(rows) > 100:
            raise ValidationError({"limit": "This history requires pagination. Specify limit=50 and follow next_cursor."})
        data = serialize(rows)
    return Response(data, headers={"Cache-Control": "private, no-store"})
