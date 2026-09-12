"""Shared catalog SQL and cursor assertions; hosts supply identity fixtures."""
from datetime import timedelta
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from apps.common import catalog_pagination
from apps.routers.models import Router


class CatalogPaginationGuards:
    def test_keyset_is_bounded_stable_and_scope_bound(self):
        Router.objects.bulk_create([Router(tenant=self.tenant, name=f"Router {n}") for n in range(120)])
        queryset = Router.objects.filter(tenant=self.tenant)
        stamp = timezone.now() - timedelta(minutes=1)
        queryset.update(created_at=stamp)
        seen = []
        cursor = ""
        for _ in range(3):
            request = self.request("/api/v1/routers/", {"limit": "50", "cursor": cursor})
            with CaptureQueriesContext(connection) as sql:
                result = catalog_pagination.page(request=request, queryset=queryset, serialize=lambda rows: [str(row.pk) for row in rows])
            self.assertEqual(len(sql), 1)
            self.assertNotIn("COUNT(", sql[0]["sql"])
            self.assertNotIn("OFFSET", sql[0]["sql"])
            seen += result["items"]
            cursor = result["next_cursor"]
            if cursor:
                other = self.request("/api/v1/routers/", {"limit": "50", "cursor": cursor, "q": "changed"})
                with self.assertRaises(Exception):
                    catalog_pagination.page(request=other, queryset=queryset, serialize=list)
        self.assertEqual(len(seen), 120)
        self.assertEqual(len(set(seen)), 120)
        self.assertIsNone(cursor)
