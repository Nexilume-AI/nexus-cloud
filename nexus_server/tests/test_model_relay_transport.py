import io
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from django.test import TestCase,override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIRequestFactory
from rest_framework import exceptions
from apps.accounts.models import RelayDevice,RelayModelBinding
from apps.accounts import relay_transport as t, relay_devices as v
from apps.providers.models import Provider,ProviderAccount,ProviderRuntimeAccount
from apps.tenancy.models import Tenant
from apps.common.crypto import encrypt_secret


@override_settings(NEXUS_PUBLIC_BASE_URL='https://cloud.example')
class ModelRelayTransportTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user(username='relay-transport-owner')
        self.tenant=Tenant.objects.create(name='Relay tenant',slug='relay-transport')
        self.provider=Provider.objects.create(name='relay-upstream')
        self.account=ProviderAccount.objects.create(tenant=self.tenant,provider=self.provider,account_id='existing',url='https://old.example/v1',encrypted_key=encrypt_secret('original-key'))
        self.runtime=ProviderRuntimeAccount.objects.create(tenant=self.tenant,source_provider_account=self.account,name='Existing',runtime_type='direct_api',status='active',internal_api_url=self.account.url)
        self.spec={'name':'Model','model':'m','endpoint':'https://upstream.example/v1','protocol':'openai-compatible','transport':'outbound','provider_id':str(self.account.pk)}
        self.device=RelayDevice.objects.create(name='Test host',user=self.user,password_hash=self.user.get_session_auth_hash(),tenant_id=str(self.tenant.pk),status='active',
            user_code_hash='c'*64,device_code_hash='d'*64,access_hash=v.digest('nrelay_a_test'),access_expires_at=timezone.now()+timedelta(minutes=10),
            approval_expires_at=timezone.now()+timedelta(minutes=10),expires_at=timezone.now()+timedelta(days=1),manifest=[self.spec])
        self.req=SimpleNamespace(user=self.user,tenant_id=str(self.tenant.pk))
        self.patches=[patch.object(t,'get_provider_connection',return_value=self.account),patch.object(v,'scope_request',return_value=self.req),patch.object(t,'scope_request',return_value=self.req)]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        self.f=APIRequestFactory()
    def call(self,view,data=None,token='nrelay_a_test',method='post'):
        req=getattr(self.f,method)('/api/v1/auth/relay/transport/',data or {},format='json',HTTP_AUTHORIZATION='Bearer '+token)
        return view.as_view()(req)
    def prepare(self):
        receipt=t.prepare(self.req,self.device,self.spec,None)
        return RelayModelBinding.objects.get(pk=receipt['binding_id'])
    def test_prepare_idempotent_existing_key_preserved_and_foreign_tenant_denied(self):
        original=self.account.encrypted_key;b=self.prepare();self.assertEqual(self.prepare().pk,b.pk)
        self.account.refresh_from_db();self.assertEqual(self.account.encrypted_key,original);self.assertEqual(self.account.url,'https://old.example/v1')
        self.device.tenant_id='foreign'
        with self.assertRaises(exceptions.PermissionDenied):self.prepare()
    def test_auth_scope_rotation_expiry_and_revocation(self):
        b=self.prepare();body={'binding_id':str(b.pk),'endpoint_api_key':'original-key'}
        self.assertEqual(self.call(t.RelayTransportAuthorize,body).status_code,200)
        self.assertEqual(self.call(t.RelayTransportAuthorize,{**body,'endpoint_api_key':'wrong'}).status_code,401)
        other=RelayDevice.objects.create(name='other',user_code_hash='e'*64,device_code_hash='f'*64,approval_expires_at=timezone.now())
        b.device=other;b.save()
        self.assertEqual(self.call(t.RelayTransportAuthorize,body).status_code,403)
        b.device=self.device;b.save();self.device.access_expires_at=timezone.now()-timedelta(seconds=1);self.device.save()
        self.assertEqual(self.call(t.RelayTransportAuthorize,body).status_code,401)
        self.device.access_expires_at=timezone.now()+timedelta(minutes=1);self.device.status='revoked';self.device.save()
        self.assertEqual(self.call(t.RelayTransportSession,method='get').status_code,401)
    def test_disabled_or_edited_provider_not_restored(self):
        b=self.prepare()
        self.account.status='disabled';self.account.save()
        self.assertEqual(self.call(t.RelayTransportAuthorize,{'binding_id':str(b.pk),'endpoint_api_key':'original-key'}).status_code,403)
        self.account.status='active';self.account.url='https://changed.example/v1';self.account.save()
        self.assertEqual(self.call(t.RelayTransportActivate,{'binding_id':str(b.pk)}).status_code,403)
    def test_cutover_requires_live_catalog_and_preserves_identity(self):
        b=self.prepare();key=self.account.encrypted_key
        with patch.object(t,'open_provider_url',side_effect=OSError()):
            self.assertEqual(self.call(t.RelayTransportActivate,{'binding_id':str(b.pk)}).status_code,400)
        self.account.refresh_from_db();self.assertEqual(self.account.url,b.original['account_url'])
        def update(**kwargs):
            self.account.url=kwargs['data']['url'];self.account.save()
        with patch.object(t,'open_provider_url',return_value=io.BytesIO(json.dumps({'data':[{'id':'m'}]}).encode())),patch.object(t,'update_provider_connection',side_effect=update),patch.object(t,'audit'):
            response=self.call(t.RelayTransportActivate,{'binding_id':str(b.pk)})
        self.assertEqual(response.status_code,200)
        self.account.refresh_from_db();self.runtime.refresh_from_db();self.assertEqual(self.account.encrypted_key,key)
        self.assertEqual(self.account.url,t.endpoint(b));self.assertEqual(self.runtime.internal_api_url,t.endpoint(b))
        self.assertEqual(ProviderAccount.objects.count(),1);self.assertEqual(RelayModelBinding.objects.count(),1)
    def test_unapproved_model_registration_and_protocol(self):
        response=self.call(v.RelayModels,{'model':'other'})
        self.assertEqual(response.status_code,403)
        invalid={**self.spec};invalid.pop('transport')
        self.assertFalse(v.Spec(data=invalid).is_valid())

    def test_concurrent_edit_during_catalog_check_is_not_overwritten(self):
        b=self.prepare()
        def catalog(*args, **kwargs):
            ProviderAccount.objects.filter(pk=self.account.pk).update(url='https://new.example/v1')
            return io.BytesIO(b'{"data":[{"id":"m"}]}')
        with patch.object(t,'open_provider_url',side_effect=catalog),patch.object(t,'update_provider_connection') as update:
            response=self.call(t.RelayTransportActivate,{'binding_id':str(b.pk)})
        self.assertEqual(response.status_code,400)
        update.assert_not_called()

    def test_activation_verification_only_runs_after_commit(self):
        b=self.prepare()
        def update(**kwargs):
            self.account.url=kwargs['data']['url'];self.account.save()
        with patch.object(t,'open_provider_url',return_value=io.BytesIO(b'{"data":[{"id":"m"}]}')), patch.object(t,'update_provider_connection',side_effect=update), patch.object(t,'audit'), patch('apps.accounts.tasks.schedule_relay_verification') as schedule:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.call(t.RelayTransportActivate,{'binding_id':str(b.pk)}).status_code,200)
                schedule.assert_not_called()
            schedule.assert_called_once_with(str(b.pk),str(self.runtime.pk))

    def test_revoked_binding_cannot_probe_and_queue_outage_uses_maintenance(self):
        from apps.accounts.tasks import verify_relay_transport, schedule_relay_verification
        b=self.prepare();b.status='revoked';b.save()
        with patch('apps.providers.runtime_services.refresh_runtime_model_offers') as refresh:
            self.assertTrue(verify_relay_transport(str(b.pk))['skipped']);refresh.assert_not_called()
        with patch('apps.accounts.tasks.verify_relay_transport.delay',side_effect=OSError()), patch('apps.providers.runtime_services.provider_catalog_refresh_result') as recover:
            schedule_relay_verification(str(b.pk),str(self.runtime.pk))
            recover.assert_called_once_with(runtime_id=str(self.runtime.pk),failed=True)
