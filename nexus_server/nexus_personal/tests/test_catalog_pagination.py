"""Actual personal model SQL; cursors never substitute for authorization."""
from datetime import timedelta
import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.common import catalog_pagination
from apps.routers.models import Router
from nexus_personal.services import provision_owner
from tests.catalog_pagination_guards import CatalogPaginationGuards
from .test_installation import PASSWORD


class PersonalCatalogPaginationTests(CatalogPaginationGuards, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.installation = provision_owner(email='catalog-owner@example.test', password=PASSWORD)
        cls.user = cls.installation.owner
        cls.tenant = cls.installation.tenant
        cls.project = cls.installation.project
        cls.other = get_user_model().objects.create_user(username='other-catalog-owner')

    def request(self, path, params=None):
        raw = APIRequestFactory().get(path, params or {})
        force_authenticate(raw, user=self.user)
        request = Request(raw)
        request.tenant_id, request.project_id = str(self.tenant.pk), str(self.project.pk)
        return request

    def seed(self, count=4):
        Router.objects.bulk_create([Router(tenant=self.tenant, project=self.project,
            created_by=self.user, name=f'Router {n}') for n in range(count)])
        self.rows().update(created_at=timezone.now() - timedelta(minutes=1))

    def rows(self):
        # This is a pre-authorized test query, not a replacement policy or API.
        return Router.objects.filter(tenant=self.tenant, project=self.project, created_by=self.user)

    def page(self, request=None, ordering='-created_at'):
        return catalog_pagination.page(request=request or self.request('/api/v1/routers/', {'limit': 1}),
            queryset=self.rows(), serialize=lambda rows: [str(row.pk) for row in rows], ordering=ordering)

    def test_cursor_identity_path_project_and_tenant_are_bound_before_sql(self):
        self.seed()
        cursor = self.page()['next_cursor']
        for changed in ('user', 'tenant', 'project', 'path'):
            with self.subTest(changed=changed):
                request = self.request('/api/v1/datasets/' if changed == 'path' else '/api/v1/routers/',
                                       {'limit': 1, 'cursor': cursor})
                if changed == 'user':
                    request.user = self.other
                elif changed == 'tenant':
                    request.tenant_id = 'different'
                elif changed == 'project':
                    request.project_id = 'different'
                with CaptureQueriesContext(connection) as queries, self.assertRaises(ValidationError):
                    self.page(request)
                self.assertEqual(len(queries), 0)

    def test_bad_expired_cursor_and_invalid_limit_never_query(self):
        self.seed()
        cursor = self.page()['next_cursor']
        for params in ({'limit': 1, 'cursor': cursor + 'tampered'}, {'limit': 0},
                       {'limit': 201}, {'limit': 'invalid'}):
            with self.subTest(params=params):
                with CaptureQueriesContext(connection) as queries, self.assertRaises(ValidationError):
                    self.page(self.request('/api/v1/routers/', params))
                self.assertEqual(len(queries), 0)
        with patch('django.core.signing.time.time', return_value=time.time() + 3601):
            with CaptureQueriesContext(connection) as queries, self.assertRaises(ValidationError):
                self.page(self.request('/api/v1/routers/', {'limit': 1, 'cursor': cursor}))
            self.assertEqual(len(queries), 0)

    def test_next_page_rechecks_filtered_rows_and_excludes_new_insertions(self):
        self.seed()
        first = self.page()
        hidden = self.rows().exclude(pk=first['items'][0]).first()
        Router.objects.filter(pk=hidden.pk).update(created_by=self.other)
        new = Router.objects.create(tenant=self.tenant, project=self.project, created_by=self.user, name='New')
        seen, cursor = list(first['items']), first['next_cursor']
        for _ in range(5):
            if not cursor:
                break
            with CaptureQueriesContext(connection) as queries:
                result = self.page(self.request('/api/v1/routers/', {'limit': 1, 'cursor': cursor}))
            self.assertEqual(len(queries), 1)
            self.assertIn('LIMIT 2', queries[0]['sql'])
            self.assertNotIn('OFFSET', queries[0]['sql'])
            self.assertNotIn('COUNT(', queries[0]['sql'])
            seen.extend(result['items'])
            cursor = result['next_cursor']
        self.assertIsNone(cursor)
        self.assertEqual(len(seen), 3)
        self.assertEqual(len(set(seen)), 3)
        self.assertNotIn(str(hidden.pk), seen)
        self.assertNotIn(str(new.pk), seen)

    def test_duplicate_names_use_primary_key_tiebreak_and_order_cannot_change(self):
        self.seed()
        self.rows().update(name='Same')
        first = self.page(ordering='name')
        request = self.request('/api/v1/routers/', {'limit': 1, 'cursor': first['next_cursor']})
        with CaptureQueriesContext(connection) as queries, self.assertRaises(ValidationError):
            self.page(request, ordering='-name')
        self.assertEqual(len(queries), 0)
        seen, cursor = list(first['items']), first['next_cursor']
        for _ in range(4):
            if not cursor:
                break
            result = self.page(self.request('/api/v1/routers/', {'limit': 1, 'cursor': cursor}), ordering='name')
            seen.extend(result['items'])
            cursor = result['next_cursor']
        self.assertIsNone(cursor)
        self.assertEqual(seen, [str(pk) for pk in self.rows().order_by('pk').values_list('pk', flat=True)])
