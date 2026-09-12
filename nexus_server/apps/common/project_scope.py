from __future__ import annotations

from django.db.models import Q, QuerySet


def current_project_id(request) -> str:
    return str(getattr(request, "project_id", "") or "").strip()


def scope_queryset_to_current_project(queryset: QuerySet, request, *, include_workspace: bool = False) -> QuerySet:
    project_id = current_project_id(request)
    if not project_id:
        return queryset
    condition = Q(project_id=project_id)
    if include_workspace:
        condition |= Q(project__isnull=True)
    return queryset.filter(condition)
