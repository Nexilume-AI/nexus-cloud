"""Opt-in, scope-bound keyset pagination for private Console directories.

Permission filtering MUST happen before this helper. Cursors are positions, never
authorization: every page is evaluated against the current authorized queryset.
"""
from __future__ import annotations

import hashlib
import json

from django.core import signing
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError

SALT = "nexus.console.catalog.v1"


def requested(request):
    return "limit" in request.query_params or "cursor" in request.query_params


def fingerprint(request):
    context = {
        "path": request.path,
        "user": str(getattr(request.user, "pk", "")),
        "tenant": str(getattr(request, "tenant_id", "") or request.headers.get("X-Nexus-Tenant", "")),
        "project": str(getattr(request, "project_id", "") or request.headers.get("X-Nexus-Project", "")),
        "filters": sorted((key, request.query_params.getlist(key)) for key in request.query_params if key not in {"cursor", "offset"}),
    }
    return hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()


def read_cursor(request):
    raw = request.query_params.get("cursor")
    if not raw:
        return None
    try:
        cursor = signing.loads(raw, salt=SALT, max_age=3600)
        if cursor["scope"] != fingerprint(request):
            raise ValueError()
        return cursor
    except (signing.BadSignature, ValueError, KeyError, TypeError) as exc:
        raise ValidationError({"cursor": "This page has expired or its filters changed. Reload the first page."}) from exc


def page(*, request, queryset, serialize, summary=None, ordering="-created_at"):
    try:
        limit = int(request.query_params.get("limit", 50))
        if not 1 <= limit <= 200:
            raise ValueError()
    except (TypeError, ValueError) as exc:
        raise ValidationError({"limit": "Use a page size between 1 and 200."}) from exc
    cursor = read_cursor(request)
    as_of = parse_datetime(cursor["as_of"]) if cursor else timezone.now()
    field = ordering.lstrip("-")
    if field not in {"created_at", "updated_at", "name"}:
        raise ValueError("Unsupported catalog ordering")
    descending = ordering.startswith("-")
    queryset = queryset.filter(created_at__lte=as_of).order_by(ordering, "-pk" if descending else "pk")
    # Only immutable ledgers should supply a summary. Live catalogs deliberately
    # omit COUNT and use one look-ahead row, avoiding a full scan on every page.
    snapshot = cursor.get("summary") if cursor else summary(queryset) if summary else None
    if cursor:
        if cursor.get("ordering", "-created_at") != ordering:
            raise ValidationError({"cursor": "Ordering changed. Reload the first page."})
        comparison = "lt" if descending else "gt"
        value = cursor.get("value", cursor["created_at"])
        queryset = queryset.filter(Q(**{f"{field}__{comparison}": value}) | Q(**{field: value, f"pk__{comparison}": cursor["pk"]}))
    rows = list(queryset[:limit + 1])
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if more:
        last = rows[-1]
        next_cursor = signing.dumps({"scope": fingerprint(request), "as_of": as_of.isoformat(),
            "created_at": last.created_at.isoformat(), "pk": str(last.pk), "summary": snapshot,
            "ordering": ordering, "value": str(getattr(last, field))}, salt=SALT, compress=True)
    result = {"items": serialize(rows), "next_cursor": next_cursor, "has_more": more, "limit": limit, "as_of": as_of.isoformat()}
    if summary:
        result["summary"] = snapshot
    return result
