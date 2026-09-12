"""Existing PostgreSQL initialization fixture's Provider contract probe.

Not a service or a general database runner. The caller must own the exact
random database; the initialization fixture retains its lifecycle and cleanup.
"""
import json
import re
from types import SimpleNamespace
import unittest
from uuid import uuid4

from django.db import connection
from apps.deployments.models import CanonicalModel
from apps.providers.models import ProviderRuntimeAccount, ProviderRuntimeModelOffer
from nexus_personal.models import PersonalInstallation
from tests.provider_probe_guardrails import ProviderProbeGuardrails
from tests.provider_recovery_concurrency_guards import ProviderRecoveryConcurrencyGuards
from apps.common.crypto import encrypt_secret
from tests.catalog_pagination_guards import CatalogPaginationGuards
from tests.provider_catalog_guards import ProviderCatalogGuards
from tests.router_catalog_guards import RouterCatalogGuards
from django.test.utils import CaptureQueriesContext
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate


class PersonalProviderProbeTests(ProviderProbeGuardrails, unittest.TestCase):
    def setUp(self):
        row = PersonalInstallation.objects.select_related('owner', 'tenant', 'project').get(slot=1)
        self.user, self.tenant, self.project = row.owner, row.tenant, row.project
        self.assertFalse(self.user.is_superuser)
        self.request = SimpleNamespace(user=self.user, tenant_id=str(row.tenant_id),
            project_id=str(row.project_id), request_id='personal-provider-guard', META={})

    def fixture_offer(self):
        # Each case owns distinct rows within the fixture-owned database, so no
        # cleanup can affect another case or an operator's existing Provider.
        key = 'guard-' + uuid4().hex
        runtime = ProviderRuntimeAccount.objects.create(tenant=self.tenant, project=self.project,
            owner=self.user, name=key, runtime_type=ProviderRuntimeAccount.RUNTIME_DIRECT_API,
            status=ProviderRuntimeAccount.STATUS_ACTIVE, internal_api_url='http://unused.invalid/v1')
        canonical = CanonicalModel.objects.create(key=key, display_name='Guard model')
        offer = ProviderRuntimeModelOffer.objects.create(runtime_account=runtime, canonical_model=canonical,
            upstream_model_id=key, health_status=ProviderRuntimeModelOffer.HEALTH_DEGRADED)
        return runtime, offer

    def test_postgres_enforces_connection_budgets(self):
        # Check the actual host connection first. Shorter budgets apply only to
        # a separate test connection, never to host settings or operator data.
        with connection.cursor() as cursor:
            for setting, value in (('lock_timeout', '5s'), ('statement_timeout', '1min'),
                                   ('idle_in_transaction_session_timeout', '2min')):
                cursor.execute('SHOW ' + setting)
                self.assertEqual(cursor.fetchone()[0], value)
        params = connection.get_connection_params()
        params['options'] += ' -c lock_timeout=100 -c statement_timeout=500'
        self.assert_postgres_connection_budgets(params)


class PersonalPaginationProbeTests(CatalogPaginationGuards, unittest.TestCase):
    def setUp(self):
        row = PersonalInstallation.objects.select_related('owner', 'tenant', 'project').get(slot=1)
        self.user, self.tenant, self.project = row.owner, row.tenant, row.project
        self.assertFalse(self.user.is_superuser)

    def request(self, path, params=None):
        raw = APIRequestFactory().get(path, params or {})
        force_authenticate(raw, user=self.user)
        request = Request(raw)
        request.tenant_id, request.project_id = str(self.tenant.pk), str(self.project.pk)
        return request


class PersonalCatalogScaleProbeTests(ProviderCatalogGuards, RouterCatalogGuards, unittest.TestCase):
    setUp = PersonalPaginationProbeTests.setUp
    request = PersonalPaginationProbeTests.request

    def test_installed_provider_summary_has_constant_queries(self):
        from apps.common import catalog_pagination
        from apps.providers.models import Provider, ProviderAccount, ProviderRuntimeHealthCheck
        from apps.providers.connection_services import list_provider_connections
        from nexus_personal.provider_serializers import PersonalProviderConnectionSerializer
        key = 'catalog-' + uuid4().hex
        provider = Provider.objects.create(name=key)
        accounts = ProviderAccount.objects.bulk_create([ProviderAccount(tenant=self.tenant,
            provider=provider, name=f'Summary {n}', account_id=f'{key}-{n}', created_by=self.user)
            for n in range(14)])
        runtimes = ProviderRuntimeAccount.objects.bulk_create([ProviderRuntimeAccount(tenant=self.tenant,
            project=self.project, owner=self.user, source_provider_account=account,
            name=f'Summary runtime {n}', runtime_type='direct_api', status='active')
            for n, account in enumerate(accounts)])
        ProviderRuntimeModelOffer.objects.bulk_create([ProviderRuntimeModelOffer(runtime_account=runtime,
            upstream_model_id=f'{key}-{n}') for n, runtime in enumerate(runtimes)])
        ProviderRuntimeHealthCheck.objects.bulk_create([ProviderRuntimeHealthCheck(runtime_account=runtime,
            tenant=self.tenant, status='healthy', reason='detail-only-health') for runtime in runtimes])

        def sample(limit):
            request = self.request('/api/v1/provider-connections/', {'limit': limit, 'projection': 'summary'})
            with CaptureQueriesContext(connection) as queries:
                result = catalog_pagination.page(request=request,
                    queryset=list_provider_connections(request=request, queryset_only=True, summary=True),
                    serialize=lambda rows: PersonalProviderConnectionSerializer(rows, many=True,
                        context={'request': request, 'summary': True}).data)
            return result, queries.captured_queries

        _, small = sample(1)
        payload, large = sample(25)
        self.assert_provider_summary_queries(small, large, payload)

    def test_installed_router_graph_has_constant_queries(self):
        from apps.routers.models import Router, RouterOutput, RouterChildBinding, RouterModelGroupBinding
        from apps.routers.services import list_routers
        from apps.deployments.models import ModelGroup
        from nexus_personal.router_serializers import RouterSerializer
        key = 'catalog-' + uuid4().hex
        routers = Router.objects.bulk_create([Router(tenant=self.tenant, project=self.project,
            created_by=self.user, name=f'{key}-{n}') for n in range(100)])
        canonical = CanonicalModel.objects.create(key=key, display_name='Catalog model')
        group = ModelGroup.objects.create(tenant=self.tenant, project=self.project,
            created_by=self.user, name=key, canonical_model=canonical)
        outputs = RouterOutput.objects.bulk_create([RouterOutput(router=row, model_group=group,
            model_name=f'model-{n}', is_default=True) for n, row in enumerate(routers)])
        RouterModelGroupBinding.objects.bulk_create([RouterModelGroupBinding(router=row,
            model_group=group, model_group_name=key) for row in routers])
        RouterChildBinding.objects.bulk_create([RouterChildBinding(router=row,
            child_output=outputs[(n+1)%100], exposed_model_name=f'child-{n}') for n, row in enumerate(routers)])
        request = self.request('/api/v1/routers/')
        queryset = list_routers(request=request).filter(pk__in=[row.pk for row in routers])
        with CaptureQueriesContext(connection) as small:
            RouterSerializer(queryset[:1], many=True, context={'request': request}).data
        with CaptureQueriesContext(connection) as large:
            payload = RouterSerializer(queryset[:100], many=True, context={'request': request}).data
        self.assert_router_catalog_queries(small, large, payload, expected=10)
        for item in payload:
            self.assertEqual(item['model_group_ids'], [str(group.pk)])
            self.assertEqual(len(item['outputs']), 1)
            self.assertEqual(len(item['child_bindings']), 1)
            self.assertIsNotNone(item['child_bindings'][0]['child_output'])


class PersonalProviderRecoveryProbeTests(ProviderRecoveryConcurrencyGuards, unittest.TestCase):
    def recovery_runtime(self):
        from django.conf import settings
        from django.core.cache import cache
        self.assertEqual(settings.CACHES['default']['BACKEND'], 'django.core.cache.backends.redis.RedisCache')
        key = 'owned-recovery-preflight-' + uuid4().hex
        try:
            self.assertTrue(cache.add(key, 'ready', timeout=30), 'Real test Redis is required')
            self.assertEqual(cache.get(key), 'ready')
        finally:
            cache.delete(key)
        row = PersonalInstallation.objects.select_related('owner', 'tenant', 'project').get(slot=1)
        self.assertFalse(row.owner.is_superuser)
        return ProviderRuntimeAccount.objects.create(
            tenant=row.tenant, owner=row.owner, project=row.project,
            name='recovery-race-' + uuid4().hex, runtime_type='codex_proxy',
            status=ProviderRuntimeAccount.STATUS_ACTIVE, container_id='old-container',
            internal_api_url='http://127.0.0.1:19080/v1',
            encrypted_proxy_api_key=encrypt_secret('stable-race-key'),
        )


def run(expected_database):
    if (not re.fullmatch(r'nexus_personal_[0-9a-f]{32}', expected_database)
            or connection.vendor != 'postgresql'
            or connection.settings_dict['NAME'] != expected_database):
        raise AssertionError('Explicit fixture-owned PostgreSQL database required')
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PersonalProviderProbeTests)
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(PersonalPaginationProbeTests))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(PersonalCatalogScaleProbeTests))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(PersonalProviderRecoveryProbeTests))
    assert suite.countTestCases() == 8
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    assert result.wasSuccessful() and not result.skipped
    print(json.dumps({'scope': 'personal-provider-postgres-guards', 'tests_run': result.testsRun,
                      'skipped': len(result.skipped), 'vendor': connection.vendor}))
