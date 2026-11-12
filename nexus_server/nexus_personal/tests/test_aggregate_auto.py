from unittest.mock import patch
from apps.routers import services
from apps.gateway import services as gateway
from .test_system_one_routing import NativeSystemOneRoutingTests


class AggregateAutoIntegrationTests(NativeSystemOneRoutingTests):
    def test_aggregate_native_rules_named_and_model_directory(self):
        second = self.configure()
        from apps.deployments.models import ModelGroup
        other=services.create_router(request=self.request(),name='second execution',model_group_ids=[ModelGroup.objects.get(name='second-pool').pk])
        services.deploy_router(request=self.request(),router_id=str(other.pk))
        services.add_router_child_binding(request=self.request(),router_id=str(self.aggregate.pk),data={'child_output_id':other.outputs.get().pk,'exposed_model_name':'fast','priority':200})
        services.update_router(request=self.request(),router_id=str(self.aggregate.pk),data={'strategy':'system_one','decision_config':self.execution.decision_config})
        self.aggregate.refresh_from_db()
        type(self).system_one_choice='model_1'
        with patch('apps.gateway.models_dev.snapshot',return_value={}):
            resolution=gateway.resolve_router_routing(request=self.request(),tenant=self.installation.tenant,router_id=str(self.aggregate.pk),payload=self.payload('auto'))
        self.assertEqual(resolution.trace['router']['decision']['reason'],'decision_model')
        self.assertEqual(len(self.system_one_payloads),1)
        import json
        criteria=self.system_one_payloads[0]['questions']['route']['criteria']
        self.assertEqual(list(criteria), ['model_1','model_0'])
        self.assertEqual(json.loads(criteria['model_1'])['role'], 'fast')
        self.assertEqual(resolution.deployments[0].pk, second.pk)
        pinned=self.resolve(self.payload('my-export'))
        self.assertEqual(pinned[0].pk,self.source_row.pk)
        self.assertEqual(len(self.system_one_payloads),1)
        self.assertIn('auto',services.router_output_model_names(router=self.aggregate))
        self.assertIn('auto',[r['id'] for r in gateway.router_model_catalog(tenant=self.installation.tenant,router=self.aggregate)])
        from apps.gateway.model_info import router_groups
        self.assertEqual(len(router_groups(self.aggregate,'auto')),2)
        # Exercise the public credential -> middleware -> accounting -> upstream path.
        created=self.post(f'/api/v1/routers/{self.aggregate.pk}/api-access/',{'action':'ensure'})
        self.assertEqual(created.status_code,200,created.data)
        from rest_framework.test import APIClient
        client=APIClient();client.credentials(HTTP_AUTHORIZATION='Bearer '+created.data['credentials']['gateway_api_key'])
        response=client.post('/api/v1/openai/v1/chat/completions',{'model':'auto','messages':[{'role':'user','content':'Hello'}]},format='json')
        self.assertEqual(response.status_code,200,response.data)
        self.assertTrue(response.data['choices'][0]['message']['content'])
        decisions=len(self.system_one_payloads)
        services.update_router(request=self.request(),router_id=str(self.aggregate.pk),data={'strategy':'task_type'})
        self.resolve(self.payload('auto'));self.assertEqual(len(self.system_one_payloads),decisions)
        services.update_router(request=self.request(),router_id=str(self.aggregate.pk),data={'strategy':'manual_priority'})
        self.aggregate.refresh_from_db();self.assertNotIn('auto',services.router_output_model_names(router=self.aggregate))

    def test_model_order_persists_and_rejects_foreign_mapping(self):
        account_id=self.decision_provider()
        binding=self.aggregate.child_bindings.get()
        config={'provider_connection_id':str(account_id),'model':'personal-model','model_order':[str(binding.pk)]}
        response=self.client.patch(f'/api/v1/routers/{self.aggregate.pk}/', {'strategy':'system_one','decision_config':config},format='json',**self.headers)
        self.assertEqual(response.status_code,200,response.data)
        self.aggregate.refresh_from_db()
        self.assertEqual(self.aggregate.decision_config['model_order'],[str(binding.pk)])
        config['model_order']=['00000000-0000-0000-0000-000000000001']
        response=self.client.patch(f'/api/v1/routers/{self.aggregate.pk}/', {'decision_config':config},format='json',**self.headers)
        self.assertEqual(response.status_code,400,response.data)
        self.aggregate.refresh_from_db()
        self.assertEqual(self.aggregate.decision_config['model_order'],[str(binding.pk)])
        self.assertEqual(len(self.system_one_payloads),0)

    def test_auto_respects_model_policy_before_decision(self):
        services.update_router(request=self.request(),router_id=str(self.aggregate.pk),data={'strategy':'task_type'})
        with patch.object(gateway,'enforce_api_key_policy',side_effect=gateway.ModelPolicyDenied):
            with self.assertRaises(gateway.DeploymentNotAvailable):self.resolve(self.payload('auto'))

    def test_auto_respects_personal_tool_credential_candidate_scope(self):
        from types import SimpleNamespace
        services.update_router(request=self.request(), router_id=str(self.aggregate.pk), data={'strategy': 'task_type'})
        scoped = SimpleNamespace(tenant_id=self.installation.tenant.pk, router_id=self.aggregate.pk,
                                 issued_for='tool_setup', model_names=['auto'])
        with patch('nexus_personal.router_credentials.current_credential', return_value=scoped):
            with self.assertRaises(gateway.DeploymentNotAvailable):
                self.resolve(self.payload('auto'))
            scoped.model_names.append('my-export')
            self.assertEqual(self.resolve(self.payload('auto'))[0].pk, self.source_row.pk)

    def test_reserved_auto_cannot_shadow_a_real_mapping(self):
        services.update_router(request=self.request(),router_id=str(self.aggregate.pk),data={'strategy':'task_type'})
        from rest_framework.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            services.update_router_child_binding(request=self.request(),router_id=str(self.aggregate.pk),binding_id=str(self.aggregate.child_bindings.get().pk),data={'exposed_model_name':'auto'})
