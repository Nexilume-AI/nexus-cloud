"""Bounded keyset pages. Signed cursors cannot be reused across scopes/filters."""
import hashlib
import json

from django.core import signing
from django.db.models import Q
from rest_framework import exceptions


def page(request, queryset, serialize, *, summary=None):
    try:
        limit = int(request.query_params.get("limit", 50))
        if not 1 <= limit <= 200:
            raise ValueError()
    except (TypeError, ValueError):
        raise exceptions.ValidationError({"limit": "Choose a page size from 1 to 200."})
    scope = hashlib.sha256(json.dumps([str(getattr(request, "tenant_id", "")), str(getattr(request, "project_id", "")),
        str(request.user.pk), request.path, sorted((key, value) for key, value in request.query_params.items() if key not in {"cursor", "limit"})]).encode()).hexdigest()
    total = queryset.count()
    cursor = request.query_params.get("cursor")
    if cursor:
        try:
            values = signing.loads(cursor, salt="dataset-page", max_age=86400)
            if values["scope"] != scope:
                raise ValueError()
            queryset = queryset.filter(Q(created_at__lt=values["created_at"]) | Q(created_at=values["created_at"], pk__lt=values["id"]))
        except (signing.BadSignature, ValueError, KeyError, TypeError):
            raise exceptions.ValidationError({"cursor": "Page cursor expired or belongs to another query. Return to the first page."})
    rows = list(queryset.order_by("-created_at", "-pk")[:limit + 1])
    more, rows = len(rows) > limit, rows[:limit]
    next_cursor = signing.dumps({"scope": scope, "created_at": rows[-1].created_at.isoformat(), "id": str(rows[-1].pk)}, salt="dataset-page") if more else None
    return {"items": [serialize(row) for row in rows], "total": total, "next_cursor": next_cursor, "summary": summary or {}}


def requested(request):
    return "limit" in request.query_params or "cursor" in request.query_params


def legacy_page(request, rows, serialize):
    """Compatibility only. Normalize historical JSON manifests before large-scale use."""
    try:
        limit = int(request.query_params.get("limit", 50))
        if not 1 <= limit <= 200:
            raise ValueError()
        scope = [request.path, str(request.user.pk), str(getattr(request, "tenant_id", "")), str(getattr(request, "project_id", ""))]
        offset = 0
        if request.query_params.get("cursor"):
            cursor = signing.loads(request.query_params["cursor"], salt="dataset-legacy-page", max_age=86400)
            if cursor["scope"] != scope:
                raise ValueError()
            offset = cursor["offset"]
        items = rows[offset:offset + limit]
        next_cursor = signing.dumps({"scope": scope, "offset": offset + limit}, salt="dataset-legacy-page") if offset + limit < len(rows) else None
        return {"items": [serialize(row) for row in items], "total": len(rows), "next_cursor": next_cursor, "summary": {}, "legacy_manifest": True}
    except (ValueError, signing.BadSignature, KeyError, TypeError):
        raise exceptions.ValidationError("Invalid or expired manifest page cursor.")
