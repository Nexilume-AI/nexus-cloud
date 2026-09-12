"""Database journal/credential safety tests; actual Computer I/O is in the SDK bridge."""
import json
import tomllib
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.utils import timezone
from django.db import IntegrityError, transaction
from rest_framework import exceptions
from apps.common.crypto import decrypt_secret
from apps.workspaces.models import ComputerRuntimeCommand
from apps.workspaces.tool_codec import tool_config_revision
from apps.workspaces.connection_core import WorkspaceConfigConflict
from nexus_personal.models import PersonalToolConfigOperation, PersonalRouterCredential
from nexus_personal import tool_apply
from .provider_http_fixture import ProviderHTTPFixture
from . import test_tool_profiles as profiles


@override_settings(ROOT_URLCONF='nexus_personal.urls', NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS='127.0.0.1')
class PersonalToolApplyTests(ProviderHTTPFixture, TestCase):
    source = profiles.PersonalToolProfileTests.source
    runtime = profiles.PersonalToolProfileTests.runtime
    post = profiles.PersonalToolProfileTests.post
    setUp = profiles.PersonalToolProfileTests.setUp

    def ready(self):
        self.device.last_seen_at = timezone.now()
        self.device.capabilities = {'tool_setup.v1':1, 'tool_setup.cas.v1':1, 'tool_setup.cas.v2':1}
        self.device.save()
        self.session.refresh_from_db()

    def req(self, key='same-request'):
        request = self.request()
        request.build_absolute_uri = lambda path: 'http://testserver' + path
        request.headers = {'Idempotency-Key':key}
        return request

    def prepare(self, *, key='same-request', content='', action='set_router', prior=None):
        self.ready()
        request = self.req(key)
        data = {'section':'api','action':action,'router_id':str(self.router.pk),'expected_revision':tool_config_revision(content)}
        request_key, digest = tool_apply.request_identity(request, data)
        return tool_apply.prepare(request=request, session=self.session, data=data, content=content,
            parsed=tomllib.loads(content), prior_profile=prior, key=request_key, digest=digest)

    def result(self, op):
        content = json.loads(decrypt_secret(op.command.encrypted_payload))['content']
        # This is a transaction/receipt unit test, not simulated hardware E2E.
        ComputerRuntimeCommand.objects.filter(pk=op.command_id).update(status='succeeded')
        return content, tomllib.loads(content)

    def finish(self, op):
        content, parsed = self.result(op)
        return tool_apply.finish(request=self.req(),session_id=self.session.pk,op_id=op.pk,content=content,parsed=parsed)

    def test_prepare_is_idempotent_and_other_writes_are_fenced(self):
        op = self.prepare(content='[mcp_servers.external]\nurl="https://external.test/mcp"\n')
        replay = self.prepare(content='[mcp_servers.external]\nurl="https://external.test/mcp"\n')
        self.assertEqual(op.pk, replay.pk)
        self.assertEqual(PersonalRouterCredential.objects.count(), 1)
        self.assertEqual(ComputerRuntimeCommand.objects.count(), 1)
        self.assertLess((op.credential.expires_at-timezone.now()).total_seconds(), 301)
        with self.assertRaises(WorkspaceConfigConflict):
            self.prepare(key='other-write')
        with self.assertRaises(WorkspaceConfigConflict):
            self.prepare(action='rotate_api_credential')
        content = json.loads(decrypt_secret(op.command.encrypted_payload))['content']
        self.assertEqual(tomllib.loads(content)['mcp_servers']['external']['url'], 'https://external.test/mcp')
        token = tomllib.loads(content)['model_providers']['nexus']['experimental_bearer_token']
        self.assertNotIn(token, str(list(PersonalToolConfigOperation.objects.values())))
        self.assertNotIn(token, str(op.command.payload_summary))

    def test_known_no_write_releases_fence_and_revokes_only_provisional_key(self):
        old = self.prepare()
        self.finish(old)
        old.profile.refresh_from_db()
        before, _ = self.result(old)
        new = self.prepare(key='rotation',content=before,action='rotate_api_credential',prior=old.profile)
        ComputerRuntimeCommand.objects.filter(pk=new.command_id).update(status='failed', error_code='TOOL_CONFIG_CONFLICT')
        tool_apply.fail_known(request=self.req(),session_id=self.session.pk,op_id=new.pk,code='TOOL_CONFIG_CONFLICT')
        old.credential.refresh_from_db()
        new.credential.refresh_from_db()
        new.refresh_from_db()
        self.assertIsNone(old.credential.revoked_at)
        self.assertIsNotNone(new.credential.revoked_at)
        self.assertFalse(new.active)
        old.profile.refresh_from_db()
        self.assertEqual(old.profile.router_credential_id,old.credential_id)

    def test_atomic_completion_promotes_key_and_retains_old_on_commit_failure(self):
        first = self.prepare()
        self.finish(first)
        first.profile.refresh_from_db()
        before, _ = self.result(first)
        op = self.prepare(key='rotate', content=before,action='rotate_api_credential',prior=first.profile)
        content, parsed = self.result(op)
        with patch('nexus_personal.tool_apply._audit', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                tool_apply.finish(request=self.req(),session_id=self.session.pk,op_id=op.pk,content=content,parsed=parsed)
        first.profile.refresh_from_db()
        first.credential.refresh_from_db()
        self.assertEqual(first.profile.router_credential_id,first.credential_id)
        self.assertIsNone(first.credential.revoked_at)
        completed = tool_apply.finish(request=self.req(),session_id=self.session.pk,op_id=op.pk,content=content,parsed=parsed)
        first.credential.refresh_from_db()
        op.credential.refresh_from_db()
        self.assertIsNotNone(first.credential.revoked_at)
        self.assertGreater((op.credential.expires_at-timezone.now()).days, 88)
        self.assertEqual(completed.state,'applied')

    def test_failed_or_changed_receipt_never_promotes_credentials(self):
        op = self.prepare()
        with self.assertRaises(WorkspaceConfigConflict):
            tool_apply.finish(request=self.req(),session_id=self.session.pk,op_id=op.pk,content='',parsed={})
        content, parsed = self.result(op)
        with self.assertRaises(WorkspaceConfigConflict):
            tool_apply.finish(request=self.req(),session_id=self.session.pk,op_id=op.pk,content=content+'\n# changed',parsed=parsed)
        op.refresh_from_db()
        self.assertTrue(op.active)
        self.assertLess((op.credential.expires_at-timezone.now()).total_seconds(),301)

    def test_prepare_rolls_back_key_profile_and_queue_on_audit_failure(self):
        with patch('nexus_personal.tool_apply._audit',side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                self.prepare()
        self.assertFalse(PersonalRouterCredential.objects.exists())
        self.assertFalse(ComputerRuntimeCommand.objects.exists())
        self.assertFalse(PersonalToolConfigOperation.objects.exists())

    def test_uncertain_canceled_command_is_not_replayed_or_treated_as_no_write(self):
        op = self.prepare()
        ComputerRuntimeCommand.objects.filter(pk=op.command_id).update(status='canceled',error_code='COMPUTER_RUNTIME_TIMEOUT')
        data = {'section':'api','action':'set_router','router_id':str(self.router.pk),'expected_revision':tool_config_revision('')}
        for _ in range(2):
            with self.assertRaises(WorkspaceConfigConflict):
                tool_apply.apply(request=self.req(),session_id=self.session.pk,data=data)
        op.refresh_from_db()
        self.assertEqual(op.state,'uncertain')
        self.assertTrue(op.active)
        self.assertEqual(ComputerRuntimeCommand.objects.count(),1)
        self.assertEqual(PersonalRouterCredential.objects.count(),1)
        with self.assertRaises(WorkspaceConfigConflict):
            tool_apply.fail_known(request=self.req(),session_id=self.session.pk,op_id=op.pk,code='TOOL_CONFIG_CONFLICT')
        op.refresh_from_db()
        self.assertTrue(op.active)
        with self.assertRaises(WorkspaceConfigConflict):
            self.prepare(key='different')

    def test_current_router_model_availability_is_rechecked_before_promotion(self):
        op = self.prepare()
        content, parsed = self.result(op)
        self.source_row.status='disabled'
        self.source_row.save()
        with self.assertRaises(exceptions.APIException):
            tool_apply.finish(request=self.req(),session_id=self.session.pk,op_id=op.pk,content=content,parsed=parsed)
        op.refresh_from_db()
        op.profile.refresh_from_db()
        self.assertTrue(op.active)
        self.assertIsNone(op.profile.router_credential_id)
        self.assertLess((op.credential.expires_at-timezone.now()).total_seconds(),301)
