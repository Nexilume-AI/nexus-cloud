"""Personal Router lists must bound SQL without trusting caller-supplied rows."""
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.exceptions import NotFound, AuthenticationFailed
from apps.routers.models import Router, RouterOutput, RouterChildBinding, RouterModelGroupBinding
from apps.routers.services import list_routers
from apps.deployments.models import ModelGroup, CanonicalModel
from apps.accounts.models import AccountProfile
from apps.tenancy.models import Tenant, Project
from nexus_personal.router_serializers import RouterSerializer, RouterOutputSerializer, RouterChildBindingSerializer
from nexus_personal.services import provision_owner
from tests.router_catalog_guards import RouterCatalogGuards
from .test_installation import PASSWORD


class PersonalRouterScaleTests(RouterCatalogGuards, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.installation = provision_owner(email='router-scale@example.test', password=PASSWORD)
        cls.user, cls.tenant, cls.project = cls.installation.owner, cls.installation.tenant, cls.installation.project
        cls.other = get_user_model().objects.create_user(username='foreign-router-owner')

    def request(self):
        raw = APIRequestFactory().get('/api/v1/routers/')
        force_authenticate(raw, user=self.user)
        request = Request(raw)
        request.tenant_id, request.project_id = str(self.tenant.pk), str(self.project.pk)
        return request

    def routers(self, count):
        return Router.objects.bulk_create([Router(tenant=self.tenant, project=self.project,
            created_by=self.user, name=f'Router {n}') for n in range(count)])

    def test_one_and_100_rows_have_fixed_revalidation_and_association_queries(self):
        self.routers(100)
        request = self.request()
        queryset = list_routers(request=request)
        with CaptureQueriesContext(connection) as small:
            RouterSerializer(queryset[:1], many=True, context={'request': request}).data
        with CaptureQueriesContext(connection) as large:
            payload = RouterSerializer(queryset[:100], many=True, context={'request': request}).data
        # Four initial prefetch queries + two current identity queries + four
        # fresh catalog queries. Enterprise's original four-query contract is
        # retained separately; Personal must revalidate the current owner.
        self.assert_router_catalog_queries(small, large, payload, expected=10)

    def test_many_outputs_and_bindings_preserve_payload_with_fixed_query_count(self):
        routers = self.routers(100)
        canonical = CanonicalModel.objects.create(key='scale-model', display_name='Scale model')
        group = ModelGroup.objects.create(tenant=self.tenant, project=self.project, created_by=self.user,
            name='Scale Pool', canonical_model=canonical)
        outputs = RouterOutput.objects.bulk_create([RouterOutput(router=row, model_group=group,
            model_name=f'model-{n}', is_default=True) for n, row in enumerate(routers)])
        RouterModelGroupBinding.objects.bulk_create([RouterModelGroupBinding(router=row, model_group=group,
            model_group_name='Scale Pool') for row in routers])
        RouterChildBinding.objects.bulk_create([RouterChildBinding(router=row, child_output=outputs[(n+1)%100],
            exposed_model_name=f'child-{n}') for n, row in enumerate(routers)])
        request = self.request()
        queryset = list_routers(request=request)
        with CaptureQueriesContext(connection) as queries:
            payload = RouterSerializer(queryset, many=True, context={'request': request}).data
        self.assertEqual(len(queries), 10)
        self.assertEqual(len(payload), 100)
        for item in payload:
            self.assertEqual(item['model_group_ids'], [str(group.pk)])
            self.assertEqual(len(item['outputs']), 1)
            self.assertEqual(len(item['child_bindings']), 1)
            self.assertIsNotNone(item['child_bindings'][0]['child_output'])

    def test_stale_names_and_forged_prefetches_are_not_serialized(self):
        row = self.routers(1)[0]
        row.name = 'forged-name'
        row.catalog_outputs = ['forged-output']
        request = self.request()
        result = RouterSerializer([row], many=True, context={'request': request}).data
        self.assertEqual(result[0]['name'], 'Router 0')
        self.assertEqual(result[0]['outputs'], [])
        foreign_tenant = Tenant.objects.create(name='Foreign catalog', slug='foreign-router-catalog')
        foreign_project = Project.objects.create(tenant=self.tenant, name='Foreign project')
        for field, changed, original in (
                ('created_by_id', self.other.pk, self.user.pk),
                ('project_id', foreign_project.pk, self.project.pk),
                ('tenant_id', foreign_tenant.pk, self.tenant.pk),
                ('status', 'deleted', row.status)):
            with self.subTest(field=field):
                Router.objects.filter(pk=row.pk).update(**{field: changed})
                try:
                    with self.assertRaises(NotFound):
                        RouterSerializer([row], many=True, context={'request': request}).data
                finally:
                    Router.objects.filter(pk=row.pk).update(**{field: original})

    def test_current_profile_disable_wins_over_cached_request(self):
        row = self.routers(1)[0]
        request = self.request()
        AccountProfile.objects.filter(user=self.user).update(status='disabled')
        with self.assertRaises(AuthenticationFailed):
            RouterSerializer([row], many=True, context={'request': request}).data

    def test_nested_visibility_matches_existing_single_related_serializers(self):
        aggregate, execution = self.routers(2)
        aggregate.router_type = 'aggregation'
        aggregate.save(update_fields=['router_type'])
        canonical = CanonicalModel.objects.create(key='nested-model', display_name='Nested model')
        group = ModelGroup.objects.create(tenant=self.tenant, project=self.project, created_by=self.user,
            name='Do not expose hidden pool', canonical_model=canonical)
        output = RouterOutput.objects.create(router=execution, model_group=group, model_name='exported')
        RouterChildBinding.objects.create(router=aggregate, child_output=output, exposed_model_name='downstream')
        context = {'request': self.request()}
        mutations = (
            (None, None, None, None, None),
            (ModelGroup, group.pk, 'created_by_id', self.other.pk, self.user.pk),
            (ModelGroup, group.pk, 'status', 'deleted', 'active'),
            (Router, execution.pk, 'status', 'deleted', execution.status),
            (RouterOutput, output.pk, 'status', 'deleted', 'active'),
        )
        for model, pk, field, value, original in mutations:
            with self.subTest(model=model.__name__ if model else 'visible', field=field):
                if model:
                    model.objects.filter(pk=pk).update(**{field: value})
                try:
                    payload = RouterSerializer([aggregate], many=True, context=context).data[0]
                    legacy_children = RouterChildBindingSerializer(aggregate.child_bindings.exclude(status='deleted')
                        .order_by('exposed_model_name', 'priority', 'created_at'), many=True, context=context).data
                    self.assertEqual(payload['child_bindings'], legacy_children)
                    if model:
                        self.assertIsNone(payload['child_bindings'][0]['child_output'])
                        self.assertEqual(payload['output_models'], [])
                        self.assertNotIn('Do not expose hidden pool', str(payload))
                    else:
                        self.assertEqual(payload['output_models'], ['downstream'])
                    if model is not Router:
                        execution_payload = RouterSerializer([execution], many=True, context=context).data[0]
                        legacy_outputs = RouterOutputSerializer(execution.outputs.exclude(status='deleted')
                            .order_by('-is_default', 'created_at'), many=True, context=context).data
                        self.assertEqual(execution_payload['outputs'], legacy_outputs)
                finally:
                    if model:
                        model.objects.filter(pk=pk).update(**{field: original})

    def test_empty_list_still_validates_owner_and_anonymous_is_rejected(self):
        self.assertEqual(RouterSerializer([], many=True, context={'request': self.request()}).data, [])
        from rest_framework.exceptions import NotAuthenticated
        with self.assertRaises(NotAuthenticated):
            RouterSerializer([], many=True).data
