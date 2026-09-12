"""Receipt/transaction tests. Real SDK writes are exercised by runtime_bridge_probe."""
import json
import tomllib
from datetime import timedelta
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import exceptions
from apps.common.crypto import encrypt_secret, decrypt_secret
from apps.workspaces.models import ComputerRuntimeCommand, WorkspaceToolManagedProfile
from apps.workspaces.connection_core import WorkspaceConfigConflict
from apps.workspaces.tool_codec import tool_config_revision
from apps.workspaces.tool_runtime import enqueue_tool_config_fenced
from nexus_personal import tool_recovery, tool_apply, router_credentials
from .provider_http_fixture import ProviderHTTPFixture
from . import test_tool_apply as base


@override_settings(ROOT_URLCONF='nexus_personal.urls', NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS='127.0.0.1')
class ToolRecoveryTests(ProviderHTTPFixture, TestCase):
    source = base.PersonalToolApplyTests.source
    runtime = base.PersonalToolApplyTests.runtime
    post = base.PersonalToolApplyTests.post
    setUp = base.PersonalToolApplyTests.setUp
    ready = base.PersonalToolApplyTests.ready
    req = base.PersonalToolApplyTests.req
    prepare = base.PersonalToolApplyTests.prepare
    result = base.PersonalToolApplyTests.result
    finish = base.PersonalToolApplyTests.finish

    def receipt(self, command, content, **overrides):
        result = {'content':content, 'revision':tool_config_revision(content),
            'fence':{'namespace':str(command.device_id), 'sequence':command.server_sequence}, **overrides}
        command.status = 'succeeded'
        command.encrypted_result = encrypt_secret(json.dumps(result))
        command.save(update_fields=['status','encrypted_result'])

    def barrier(self, op, content):
        op = tool_recovery.begin(request=self.req(),session=self.session,operation_id=op.pk)
        self.receipt(op.fence_command,content)
        return op

    def test_barrier_can_promote_confirmed_file_despite_lost_original_receipt(self):
        op = self.prepare()
        content = json.loads(decrypt_secret(op.command.encrypted_payload))['content']
        ComputerRuntimeCommand.objects.filter(pk=op.command_id).update(status='canceled')
        op = self.barrier(op,content)
        with self.assertRaises(WorkspaceConfigConflict):
            tool_apply.finish(request=self.req(),session_id=self.session.pk,op_id=op.pk,content=content,parsed=tomllib.loads(content))
        completed = tool_apply.finish(request=self.req(),session_id=self.session.pk,op_id=op.pk,
            content=content,parsed=tomllib.loads(content),recovery=True)
        self.assertEqual(completed.state,'applied')
        self.assertEqual(router_credentials._current(self.req(),op.credential_id)[0].pk,op.credential_id)

    def test_no_write_snapshot_closes_without_promoting_provisional_key(self):
        op = self.barrier(self.prepare(),'')
        done = tool_recovery.close_pending(request=self.req(),session=self.session,operation_id=op.pk,
            proof_id=op.fence_command_id,content='',state='rolled_back')
        self.assertFalse(done.active)
        op.credential.refresh_from_db()
        self.assertIsNotNone(op.credential.revoked_at)
        op.profile.refresh_from_db()
        self.assertIsNone(op.profile.router_credential_id)

    def test_old_or_malformed_barrier_never_authorizes_recovery(self):
        op = self.prepare()
        content = json.loads(decrypt_secret(op.command.encrypted_payload))['content']
        first = self.barrier(op,content)
        latest = self.barrier(first,content)
        with self.assertRaises(WorkspaceConfigConflict):
            tool_recovery.verify_barrier(latest,first.fence_command,content)
        for fence in [{'namespace':'wrong','sequence':latest.fence_command.server_sequence},
                {'namespace':str(self.device.pk),'sequence':op.command.server_sequence}]:
            self.receipt(latest.fence_command,content,fence=fence)
            with self.assertRaises(WorkspaceConfigConflict):
                tool_recovery.verify_barrier(latest,latest.fence_command,content)

    def test_legacy_cas_is_not_misrepresented_as_recoverable(self):
        op = self.prepare()
        ComputerRuntimeCommand.objects.filter(pk=op.command_id).update(operation='tool_setup.write_codex_config_cas')
        with self.assertRaises(WorkspaceConfigConflict):
            tool_recovery.begin(request=self.req(),session=self.session,operation_id=op.pk)
        self.assertEqual(ComputerRuntimeCommand.objects.count(),1)

    def test_expired_provisional_key_requires_compensation_not_promotion(self):
        first = self.prepare()
        self.finish(first)
        before,_ = self.result(first)
        first.profile.refresh_from_db()
        op = self.prepare(key='rotate',content=before,action='rotate_api_credential',prior=first.profile)
        target = json.loads(decrypt_secret(op.command.encrypted_payload))['content']
        op.credential.expires_at = timezone.now()-timedelta(seconds=1)
        op.credential.save(update_fields=['expires_at'])
        op = self.barrier(op,target)
        with self.assertRaises(WorkspaceConfigConflict):
            tool_apply.finish(request=self.req(),session_id=self.session.pk,op_id=op.pk,
                content=target,parsed=tomllib.loads(target),recovery=True)
        restore = tool_recovery.prepare_restore(request=self.req(),session=self.session,operation_id=op.pk,
            barrier_id=op.fence_command_id,content=target)
        payload = json.loads(decrypt_secret(restore.restore_command.encrypted_payload))
        self.assertEqual(payload['content'],before)
        self.assertGreater(payload['sequence'],op.fence_command.server_sequence)
        self.receipt(restore.restore_command,before)
        done = tool_recovery.close_pending(request=self.req(),session=self.session,operation_id=op.pk,
            proof_id=restore.restore_command_id,content=before,state='rolled_back',restored=True)
        self.assertFalse(done.active)
        self.assertEqual(router_credentials._current(self.req(),first.credential_id)[0].pk,first.credential_id)

    def test_local_edits_and_revoked_backup_key_are_not_overwritten(self):
        first = self.prepare()
        self.finish(first)
        before,_ = self.result(first)
        first.profile.refresh_from_db()
        op = self.prepare(key='rotate',content=before,action='rotate_api_credential',prior=first.profile)
        target = json.loads(decrypt_secret(op.command.encrypted_payload))['content']
        op = self.barrier(op,target+'\n# user edit')
        with self.assertRaises(WorkspaceConfigConflict):
            tool_recovery.prepare_restore(request=self.req(),session=self.session,operation_id=op.pk,
                barrier_id=op.fence_command_id,content=target+'\n# user edit')
        first.credential.revoked_at = timezone.now()
        first.credential.save(update_fields=['revoked_at'])
        op = self.barrier(op,target)
        with self.assertRaises(WorkspaceConfigConflict):
            tool_recovery.prepare_restore(request=self.req(),session=self.session,operation_id=op.pk,
                barrier_id=op.fence_command_id,content=target)
        self.assertFalse(ComputerRuntimeCommand.objects.filter(idempotency_key__startswith='personal-tool-restore-').exists())

    def test_command_idempotency_never_overwrites_queued_payload(self):
        op = self.prepare()
        payload = json.loads(decrypt_secret(op.command.encrypted_payload))
        body = {key:value for key,value in payload.items() if key not in {'namespace','sequence'}}
        repeated = enqueue_tool_config_fenced(connection=self.session.connection,payload=body,idempotency_key=op.command.idempotency_key)
        self.assertEqual(repeated.pk,op.command_id)
        with self.assertRaises(WorkspaceConfigConflict):
            enqueue_tool_config_fenced(connection=self.session.connection,payload={**body,'content':'model="different"'},idempotency_key=op.command.idempotency_key)
        op.command.refresh_from_db()
        self.assertEqual(json.loads(decrypt_secret(op.command.encrypted_payload)),payload)

    def test_deleted_original_receipt_rejects_compensation_settlement(self):
        op = self.prepare()
        target = json.loads(decrypt_secret(op.command.encrypted_payload))['content']
        op = self.barrier(op,target)
        op = tool_recovery.prepare_restore(request=self.req(),session=self.session,operation_id=op.pk,
            barrier_id=op.fence_command_id,content=target)
        self.receipt(op.restore_command,'')
        ComputerRuntimeCommand.objects.filter(pk=op.command_id).delete()
        with self.assertRaises(WorkspaceConfigConflict):
            tool_recovery.close_pending(request=self.req(),session=self.session,operation_id=op.pk,
                proof_id=op.restore_command_id,content='',state='rolled_back',restored=True)

    def test_profile_deletion_cancels_barrier_and_compensation(self):
        op = self.prepare()
        target = json.loads(decrypt_secret(op.command.encrypted_payload))['content']
        op = self.barrier(op,target)
        op = tool_recovery.prepare_restore(request=self.req(),session=self.session,operation_id=op.pk,
            barrier_id=op.fence_command_id,content=target)
        ids = [op.command_id,op.restore_command_id]
        # Also cover an in-flight barrier rather than only a completed receipt.
        ComputerRuntimeCommand.objects.filter(pk=op.fence_command_id).update(status='queued')
        ids.append(op.fence_command_id)
        with self.captureOnCommitCallbacks(execute=True):
            WorkspaceToolManagedProfile.objects.filter(pk=op.profile_id).delete()
        self.assertEqual(set(ComputerRuntimeCommand.objects.filter(pk__in=ids).values_list('status',flat=True)),{'canceled'})

    def test_recovery_http_denies_other_owner_and_invalid_action_without_queueing(self):
        op = self.prepare()
        url = f'/api/v1/workspace-terminal-sessions/{self.session.pk}/tool-config/recovery/'
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(url,{'operation_id':str(op.pk),'expected_revision':op.expected_revision,'action':'recover'},format='json',**self.headers).status_code,401)
        self.client.force_login(self.installation.owner)
        self.assertEqual(self.client.post(url,{'operation_id':str(op.pk),'expected_revision':op.expected_revision,'action':'force'},format='json',**self.headers).status_code,400)
        self.assertEqual(ComputerRuntimeCommand.objects.count(),1)
