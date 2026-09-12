"""Live capability checks and committed lifecycle cleanup; no remote runner."""
import importlib
import json
from datetime import timedelta
from types import SimpleNamespace
from django.apps import apps
from django.db import connection, transaction, IntegrityError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import exceptions
from apps.common.crypto import decrypt_secret
from apps.workspaces.models import WorkspaceConnection, WorkspaceToolManagedProfile, ComputerRuntimeDevice, ComputerRuntimeCommand
from nexus_personal.models import PersonalRouterCredential
from nexus_personal import tool_apply, tool_credentials, router_credentials
from .provider_http_fixture import ProviderHTTPFixture
from . import test_tool_apply as apply_tests


@override_settings(ROOT_URLCONF='nexus_personal.urls',NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS='127.0.0.1')
class ToolCredentialLifecycleTests(ProviderHTTPFixture,TestCase):
    source=apply_tests.PersonalToolApplyTests.source
    runtime=apply_tests.PersonalToolApplyTests.runtime
    post=apply_tests.PersonalToolApplyTests.post
    setUp=apply_tests.PersonalToolApplyTests.setUp
    ready=apply_tests.PersonalToolApplyTests.ready
    req=apply_tests.PersonalToolApplyTests.req
    prepare=apply_tests.PersonalToolApplyTests.prepare
    result=apply_tests.PersonalToolApplyTests.result
    finish=apply_tests.PersonalToolApplyTests.finish

    def applied(self):
        op=self.prepare()
        self.finish(op)
        op.credential.refresh_from_db()
        return op

    def assert_valid(self,credential):
        self.assertEqual(router_credentials._current(self.req(),credential.pk)[0].pk,credential.pk)

    def assert_denied(self,credential):
        with self.assertRaises(exceptions.APIException):
            router_credentials._current(self.req(),credential.pk)

    def test_provisional_key_denied_until_verified_and_offline_is_not_revocation(self):
        op=self.prepare()
        self.assert_denied(op.credential)
        self.finish(op)
        self.assert_valid(op.credential)
        ComputerRuntimeDevice.objects.filter(pk=self.device.pk).update(last_seen_at=timezone.now()-timedelta(days=1))
        self.assert_valid(op.credential)
        ComputerRuntimeDevice.objects.filter(pk=self.device.pk).update(revoked_at=timezone.now())
        self.assert_denied(op.credential)  # QuerySet.update emits no signals.

    def test_revoke_http_invalidates_only_this_computers_managed_key(self):
        op=self.applied()
        exported=router_credentials.export_router_credentials(request=self.req(),router_id=self.router.pk)
        ordinary=PersonalRouterCredential.objects.get(pk=exported['gateway_api_key_id'])
        with self.captureOnCommitCallbacks(execute=True):
            response=self.post(f'/api/v1/computers/{self.computer.pk}/revoke/')
        self.assertEqual(response.status_code,200,response.data)
        op.credential.refresh_from_db()
        self.assertIsNotNone(op.credential.revoked_at)
        self.assert_denied(op.credential)
        self.assert_valid(ordinary)
        self.assertNotIn(exported['gateway_api_key'],str(response.data))

    def test_pending_operation_cannot_promote_after_computer_revocation(self):
        op=self.prepare()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.post(f'/api/v1/computers/{self.computer.pk}/revoke/').status_code,200)
        op.refresh_from_db()
        self.assertEqual(op.state,'revoked')
        self.assertFalse(op.active)
        self.assertEqual(ComputerRuntimeCommand.objects.get(pk=op.command_id).status,'canceled')
        with self.assertRaises(exceptions.APIException):
            self.finish(op)
        self.assert_denied(op.credential)

    def test_delete_http_requires_closed_terminal_then_cleans_bound_keys(self):
        op=self.applied()
        url=f'/api/v1/workspace-connections/{self.computer.pk}/'
        self.assertEqual(self.client.delete(url,**self.headers).status_code,400)
        self.assert_valid(op.credential)
        self.session.status='closed'
        self.session.save(update_fields=['status'])
        with self.captureOnCommitCallbacks(execute=True):
            response=self.client.delete(url,**self.headers)
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(response.data['status'],'deleted')
        op.credential.refresh_from_db()
        self.assertIsNotNone(op.credential.revoked_at)
        self.assertEqual(op.credential.issued_for,'tool_setup')
        self.assertEqual(op.credential.tool_connection_id,self.computer.pk)
        self.assert_denied(op.credential)

    def test_hard_deleted_profile_retains_key_binding_and_cancels_pending_command(self):
        op=self.prepare()
        command_id=op.command_id
        with self.captureOnCommitCallbacks(execute=True):
            WorkspaceToolManagedProfile.objects.filter(pk=op.profile_id).delete()
        op.credential.refresh_from_db()
        self.assertIsNotNone(op.credential.revoked_at)
        self.assertEqual(op.credential.tool_profile_id,op.profile_id)
        self.assertEqual(ComputerRuntimeCommand.objects.get(pk=command_id).status,'canceled')
        self.assert_denied(op.credential)

    def test_owner_change_and_wrong_pairing_are_rejected_even_without_signals(self):
        op=self.applied()
        WorkspaceConnection.objects.filter(pk=self.computer.pk).update(created_by=self.other)
        self.assert_denied(op.credential)
        WorkspaceConnection.objects.filter(pk=self.computer.pk).update(created_by=self.installation.owner)
        self.assert_valid(op.credential)
        with self.captureOnCommitCallbacks(execute=True):
            ComputerRuntimeDevice.objects.filter(pk=self.device.pk).delete()
        op.credential.refresh_from_db()
        self.assertIsNotNone(op.credential.revoked_at)
        self.assertEqual(op.credential.tool_device_id,self.device.pk)

    def test_rolled_back_revoke_neither_revokes_key_nor_runs_cleanup(self):
        op=self.applied()
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    self.device.revoked_at=timezone.now()
                    self.device.save(update_fields=['revoked_at'])
                    raise RuntimeError('rollback')
            except RuntimeError:
                pass
        op.credential.refresh_from_db()
        self.assertIsNone(op.credential.revoked_at)
        self.assert_valid(op.credential)

    def test_origin_constraint_cannot_turn_deleted_binding_into_exported_key(self):
        op=self.applied()
        with self.assertRaises(IntegrityError),transaction.atomic():
            PersonalRouterCredential.objects.filter(pk=op.credential_id).update(tool_device_id=None)
        self.assert_valid(op.credential)

    def test_backfill_adopts_only_proven_issuance_and_rejects_missing_receipts(self):
        op=self.prepare()
        PersonalRouterCredential.objects.filter(pk=op.credential_id).update(issued_for='exported',
            tool_device_id=None,tool_profile_id=None,tool_connection_id=None)
        ordinary=router_credentials.export_router_credentials(request=self.req(),router_id=self.router.pk)
        migration=importlib.import_module('nexus_personal.migrations.0009_router_credential_computer_binding')
        migration.bind_existing_tool_keys(apps,SimpleNamespace(connection=connection))
        op.credential.refresh_from_db()
        self.assertEqual(op.credential.tool_device_id,self.device.pk)
        self.assertIsNone(op.credential.revoked_at)
        self.assertEqual(PersonalRouterCredential.objects.get(pk=ordinary['gateway_api_key_id']).issued_for,'exported')
        PersonalRouterCredential.objects.filter(pk=op.credential_id).update(issued_for='exported',
            tool_device_id=None,tool_profile_id=None,tool_connection_id=None)
        op.command=None
        op.save(update_fields=['command'])
        migration.bind_existing_tool_keys(apps,SimpleNamespace(connection=connection))
        op.credential.refresh_from_db()
        self.assertIsNotNone(op.credential.revoked_at)
        self.assertEqual(op.credential.issued_for,'tool_setup')

    def test_downgrade_revokes_tool_keys_without_affecting_exports_or_old_revocation_dates(self):
        op=self.applied()
        ordinary=router_credentials.export_router_credentials(request=self.req(),router_id=self.router.pk)
        migration=importlib.import_module('nexus_personal.migrations.0009_router_credential_computer_binding')
        migration.revoke_before_unbinding(apps,SimpleNamespace(connection=connection))
        op.credential.refresh_from_db()
        revoked_at=op.credential.revoked_at
        self.assertIsNotNone(revoked_at)
        migration.revoke_before_unbinding(apps,SimpleNamespace(connection=connection))
        op.credential.refresh_from_db()
        self.assertEqual(op.credential.revoked_at,revoked_at)
        self.assertIsNone(PersonalRouterCredential.objects.get(pk=ordinary['gateway_api_key_id']).revoked_at)
