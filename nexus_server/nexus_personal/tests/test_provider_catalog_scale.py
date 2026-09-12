"""Actual Personal Provider API and SQL with the shared Enterprise query bounds."""
from django.db import connection
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.request import Request
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate
from rest_framework.exceptions import NotFound, AuthenticationFailed

from apps.common import catalog_pagination
from apps.providers.connection_services import get_provider_connection, list_provider_connections
from nexus_personal.provider_serializers import PersonalProviderConnectionSerializer as ProviderConnectionSerializer
from apps.providers.models import (Provider, ProviderAccount, ProviderRuntimeAccount,
                                  ProviderRuntimeModelOffer, ProviderRuntimeHealthCheck)
from nexus_personal.services import provision_owner
from apps.accounts.models import AccountProfile
from apps.tenancy.models import Project
from tests.provider_catalog_guards import ProviderCatalogGuards
from .test_installation import PASSWORD


@override_settings(ROOT_URLCONF='nexus_personal.urls')
class PersonalProviderScaleTests(ProviderCatalogGuards, TestCase):
    base = '/api/v1/provider-connections/'

    @classmethod
    def setUpTestData(cls):
        cls.installation = provision_owner(email='provider-scale@example.test', password=PASSWORD)
        cls.user, cls.tenant, cls.project = cls.installation.owner, cls.installation.tenant, cls.installation.project
        cls.provider = Provider.objects.create(name='scale-fixture')

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)

    def request(self, params=None):
        raw = APIRequestFactory().get(self.base, params or {})
        force_authenticate(raw, user=self.user)
        request = Request(raw)
        request.tenant_id, request.project_id = str(self.tenant.pk), str(self.project.pk)
        return request

    def seed(self, count):
        accounts = ProviderAccount.objects.bulk_create([ProviderAccount(tenant=self.tenant, provider=self.provider,
            name=f'Summary {n}', account_id=f'p-{n}', created_by=self.user) for n in range(count)])
        runtimes = ProviderRuntimeAccount.objects.bulk_create([ProviderRuntimeAccount(tenant=self.tenant,
            project=self.project, owner=self.user, source_provider_account=account, name=f'Runtime {n}',
            runtime_type='direct_api', status='active') for n, account in enumerate(accounts)])
        return accounts, runtimes

    def test_detail_prefetch_is_limited_to_requested_account_among_100(self):
        self.seed(100)
        with CaptureQueriesContext(connection) as queries:
            account = get_provider_connection(request=self.request(), account_id='p-23')
        self.assert_provider_detail_prefetch(account, queries)

    def test_summary_cost_is_constant_and_omits_detail_history(self):
        _, runtimes = self.seed(14)
        for n, runtime in enumerate(runtimes):
            ProviderRuntimeModelOffer.objects.create(runtime_account=runtime, upstream_model_id=f'only-model-{n}')
            ProviderRuntimeHealthCheck.objects.create(runtime_account=runtime, tenant=self.tenant,
                status='healthy', reason='detail-only-health')
        def sample(limit):
            request = self.request({'limit': limit, 'projection': 'summary'})
            with CaptureQueriesContext(connection) as queries:
                result = catalog_pagination.page(request=request,
                    queryset=list_provider_connections(request=request, queryset_only=True, summary=True),
                    serialize=lambda rows: ProviderConnectionSerializer(rows, many=True,
                        context={'request': request, 'summary': True}).data)
            return result, queries.captured_queries
        _, small = sample(1)
        result, large = sample(25)
        self.assert_provider_summary_queries(small, large, result)
        response = self.client.get(self.base, {'limit': 25, 'projection': 'summary', 'q': 'only-model-13'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([item['name'] for item in response.data['items']], ['Summary 13'])
        self.assertEqual(response.data['items'][0]['models'], [])

    def test_real_detail_api_limits_history_and_preserves_models(self):
        accounts, runtimes = self.seed(1)
        ProviderRuntimeModelOffer.objects.create(runtime_account=runtimes[0], upstream_model_id='detail-model')
        ProviderRuntimeHealthCheck.objects.bulk_create([ProviderRuntimeHealthCheck(runtime_account=runtimes[0],
            tenant=self.tenant, status='healthy', reason=f'check-{n}') for n in range(20)])
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.base + str(accounts[0].pk) + '/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assert_provider_detail_history(response.data, queries)
        legacy = self.client.get(self.base, {'limit': 25})
        self.assertEqual(legacy.status_code, 200, legacy.data)
        self.assertEqual(legacy.data['items'][0]['models'], response.data['models'])

    def test_filters_precede_pagination_in_actual_owner_api(self):
        _, runtimes = self.seed(3)
        ProviderRuntimeAccount.objects.filter(pk__in=[r.pk for r in runtimes[:2]]).update(status='stopped')
        response = self.client.get(self.base, {'limit': 1, 'status': 'healthy', 'q': 'Summary'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([item['name'] for item in response.data['items']], ['Summary 2'])
        self.assertFalse(response.data['has_more'])

    def test_batch_reloads_payload_and_rejects_changed_ownership(self):
        accounts, runtimes = self.seed(2)
        request = self.request({'projection': 'summary'})
        rows = list(list_provider_connections(request=request, queryset_only=True, summary=True))
        for row in rows:
            row.name = 'forged-stale-name'
            row.prefetched_connection_runtimes = []
        ProviderAccount.objects.filter(pk=accounts[0].pk).update(name='Current name')
        def render():
            return ProviderConnectionSerializer(rows, many=True, context={'request': request, 'summary': True}).data
        rendered = render()
        self.assertEqual({item['name'] for item in rendered}, {'Current name', 'Summary 1'})
        self.assertNotIn('forged-stale-name', str(rendered))
        other = get_user_model().objects.create_user(username='other-scale-owner')
        project = Project.objects.create(tenant=self.tenant, name='Other project')
        mutations = (
            (ProviderAccount, accounts[0].pk, 'created_by_id', other.pk, self.user.pk),
            (ProviderRuntimeAccount, runtimes[0].pk, 'owner_id', other.pk, self.user.pk),
            (ProviderRuntimeAccount, runtimes[0].pk, 'project_id', project.pk, self.project.pk),
            (ProviderAccount, accounts[0].pk, 'status', 'deleted', accounts[0].status),
            (ProviderRuntimeAccount, runtimes[0].pk, 'status', 'deleted', 'active'),
        )
        for model, pk, field, value, original in mutations:
            with self.subTest(model=model.__name__, field=field):
                model.objects.filter(pk=pk).update(**{field: value})
                try:
                    with self.assertRaises(NotFound):
                        render()
                finally:
                    model.objects.filter(pk=pk).update(**{field: original})

    def test_batch_owner_and_profile_disable_override_cached_request_user(self):
        self.seed(1)
        request = self.request({'projection': 'summary'})
        rows = list(list_provider_connections(request=request, queryset_only=True, summary=True))
        for profile in (True, False):
            with self.subTest(profile=profile):
                if profile:
                    AccountProfile.objects.filter(user=self.user).update(status='disabled')
                else:
                    get_user_model().objects.filter(pk=self.user.pk).update(is_active=False)
                try:
                    with self.assertRaises(AuthenticationFailed):
                        ProviderConnectionSerializer(rows, many=True, context={'request': request, 'summary': True}).data
                finally:
                    AccountProfile.objects.filter(user=self.user).update(status='active')
                    get_user_model().objects.filter(pk=self.user.pk).update(is_active=True)
