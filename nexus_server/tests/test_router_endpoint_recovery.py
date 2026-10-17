from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from apps.common.crypto import encrypt_secret
from apps.deployments.models import CanonicalModel, Deployment
from apps.deployments.health import check_deployment_health
from apps.deployments.tasks import check_all_active_deployments, check_one_deployment_health
from apps.gateway.provider_adapters import ProviderHealthResult, managed_provider_endpoint
from apps.providers.models import Provider, ProviderAccount, ProviderRuntimeAccount, ProviderRuntimeModelOffer
from apps.providers.runtime_runner import ProviderRuntimeHealthResult, ProviderRuntimeStartResult
from apps.providers.runtime_services import reconcile_provider_runtime_health, _sync_provider_runtime_endpoint, start_provider_runtime
from apps.tenancy.models import Tenant, Membership


class RouterEndpointRecoveryTests(TestCase):
    def setUp(self):
        from nexus_personal.services import provision_owner
        row=provision_owner(email='router-recovery@example.test',password='Strong-test-password-765!')
        self.owner,self.tenant=row.owner,row.tenant
        self.provider=Provider.objects.create(name='openai')
        self.runtime=ProviderRuntimeAccount.objects.create(tenant=self.tenant,project=row.project,owner=self.owner,name='Managed',
            runtime_type='cliproxyapi',status='active',internal_api_url='http://127.0.0.1:32781/v1',
            encrypted_proxy_api_key=encrypt_secret('fixture-proxy-key'))
        self.account=ProviderAccount.objects.create(tenant=self.tenant,provider=self.provider,
            account_id=f'runtime_{self.runtime.id}',url='http://127.0.0.1:32772/v1',encrypted_key=self.runtime.encrypted_proxy_api_key)
        self.runtime.provider_account=self.account;self.runtime.save()
        self.model=CanonicalModel.objects.create(key='recovery-model',display_name='Recovery')
        self.offer=ProviderRuntimeModelOffer.objects.create(runtime_account=self.runtime,canonical_model=self.model,
            upstream_model_id='recovery-model',status='confirmed',health_status='healthy')
        self.source=Deployment.objects.create(tenant=self.tenant,provider=self.provider,provider_account=self.account,
            deployment_id='managed-source',canonical_model=self.model,upstream_model_id='recovery-model',
            provider_runtime=self.runtime,runtime_model_offer=self.offer,endpoint=self.account.url,
            health_status='unhealthy',health_reason='Provider endpoint must use HTTPS.',consecutive_failures=489)

    def test_explicit_restart_synchronizes_new_port_before_model_discovery(self):
        from types import SimpleNamespace
        self.runtime.status='stopped';self.runtime.save()
        with patch('apps.providers.runtime_services.get_provider_runtime',return_value=self.runtime), \
                patch('apps.providers.runtime_services.require_provider_runtime_admin'), \
                patch('apps.providers.runtime_services.get_provider_runtime_runner') as runner, \
                patch('apps.providers.runtime_services.login_runtime_if_required',return_value='active'), \
                patch('apps.providers.runtime_services.log_runtime_write'), \
                patch('apps.providers.runtime_services.refresh_runtime_model_offers') as discover, \
                patch('apps.deployments.tasks.check_one_deployment_health.delay') as delay:
            runner.return_value.start.return_value=ProviderRuntimeStartResult('new-container','','http://127.0.0.1:32799/v1')
            def check_binding(**kwargs):
                self.source.refresh_from_db();self.account.refresh_from_db()
                self.assertEqual(self.source.endpoint,'http://127.0.0.1:32799/v1')
                self.assertEqual(self.account.url,self.source.endpoint)
            discover.side_effect=check_binding
            with self.captureOnCommitCallbacks(execute=True):
                recovered=start_provider_runtime(request=SimpleNamespace(user=self.owner),runtime_id=str(self.runtime.id))
            self.assertEqual(recovered.status,'active')
            delay.assert_called_once_with(str(self.source.id))

    @patch('apps.providers.runtime_services.get_provider_runtime_runner')
    @patch('apps.deployments.tasks.check_one_deployment_health.delay')
    def test_healthy_periodic_check_repairs_drift_then_real_probe_recovers(self, delay, factory):
        factory.return_value.health_check.return_value=ProviderRuntimeHealthResult(True,False,'ready')
        with self.captureOnCommitCallbacks(execute=True):
            recovered=reconcile_provider_runtime_health(runtime_id=self.runtime.id)
        self.source.refresh_from_db();self.account.refresh_from_db()
        self.assertEqual(self.source.endpoint,recovered.internal_api_url)
        self.assertEqual(self.account.url,recovered.internal_api_url)
        self.assertTrue(managed_provider_endpoint(deployment=self.source,url=self.source.endpoint))
        self.assertFalse(managed_provider_endpoint(deployment=self.source,url='http://127.0.0.1:32772/v1'))
        self.assertEqual(self.source.health_status,'unhealthy')
        delay.assert_called_once_with(str(self.source.id))
        factory.return_value.restore.assert_not_called()
        with patch('apps.deployments.health.OpenAICompatibleClient.check_health',return_value=ProviderHealthResult(True,False,'reachable',3)):
            result=check_one_deployment_health(str(self.source.id))
        self.assertEqual(result['status'],'healthy')
        self.source.refresh_from_db();self.assertEqual(self.source.consecutive_failures,0)
        delay.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            reconcile_provider_runtime_health(runtime_id=self.runtime.id)
        delay.assert_not_called()

    def test_sync_does_not_reenable_disabled_or_deleted_sources(self):
        self.source.status='disabled';self.source.save()
        with patch('apps.deployments.tasks.check_one_deployment_health.delay') as delay,self.captureOnCommitCallbacks(execute=True):
            _sync_provider_runtime_endpoint(runtime=self.runtime)
        self.source.refresh_from_db();self.assertEqual(self.source.status,'disabled');delay.assert_not_called()
        self.source.status='deleted';self.source.endpoint='http://127.0.0.1:32772/v1';self.source.save()
        _sync_provider_runtime_endpoint(runtime=self.runtime)
        self.source.refresh_from_db();self.assertEqual(self.source.endpoint,'http://127.0.0.1:32772/v1')
        self.assertTrue(check_one_deployment_health(str(self.source.id))['skipped'])

    def test_direct_or_stopped_runtime_cannot_rebind_sources(self):
        for kind,status in [('direct_api','active'),('cliproxyapi','stopped'),('cliproxyapi','stopping')]:
            self.runtime.runtime_type=kind;self.runtime.status=status
            _sync_provider_runtime_endpoint(runtime=self.runtime)
            self.source.refresh_from_db();self.assertEqual(self.source.endpoint,'http://127.0.0.1:32772/v1')

    @patch('apps.deployments.health.OpenAICompatibleClient.check_health')
    def test_removed_or_failed_offer_is_not_green_from_catalog_reachability(self, probe):
        for status,health in [('unavailable','healthy'),('disabled','healthy'),('confirmed','unhealthy')]:
            self.offer.status=status;self.offer.health_status=health;self.offer.save()
            self.source.refresh_from_db()
            self.assertEqual(check_deployment_health(deployment=self.source).status,'unhealthy')
        probe.assert_not_called()

    @patch('apps.deployments.health.OpenAICompatibleClient.check_health')
    def test_stopped_provider_is_not_resurrected_by_source_probe(self, probe):
        self.runtime.status='stopped';self.runtime.save();self.source.refresh_from_db()
        self.assertEqual(check_deployment_health(deployment=self.source).status,'unhealthy');probe.assert_not_called()

    @patch('apps.deployments.health.OpenAICompatibleClient.check_health',return_value=ProviderHealthResult(True,False,'reachable',3))
    def test_pending_model_stays_pending_until_model_verification_recovers(self, probe):
        for health in ['unknown','degraded','healthy']:
            self.offer.health_status=health;self.offer.save();self.source.refresh_from_db()
            self.assertEqual(check_deployment_health(deployment=self.source).status,health)

    @patch('apps.deployments.tasks.check_deployment_health')
    def test_one_probe_exception_does_not_block_remaining_sources(self, probe):
        other=Deployment.objects.create(tenant=self.tenant,provider=self.provider,provider_account=self.account,
            deployment_id='other-source',canonical_model=self.model,upstream_model_id='recovery-model')
        from types import SimpleNamespace
        probe.side_effect=[RuntimeError('private diagnostic'),SimpleNamespace(deployment=other,status='healthy')]
        result=check_all_active_deployments()
        self.assertEqual(result['checked'],1);self.assertEqual(result['errors'],1)
        self.assertEqual(probe.call_count,2)

    @patch('apps.providers.runtime_services.get_provider_runtime_runner')
    def test_stale_probe_cannot_rebind_after_concurrent_stop(self, factory):
        def stop(**kwargs):
            ProviderRuntimeAccount.objects.filter(pk=self.runtime.pk).update(status='stopped')
            return ProviderRuntimeHealthResult(True,False,'ready')
        factory.return_value.health_check.side_effect=stop
        reconcile_provider_runtime_health(runtime_id=self.runtime.id)
        self.source.refresh_from_db();self.assertEqual(self.source.endpoint,'http://127.0.0.1:32772/v1')
