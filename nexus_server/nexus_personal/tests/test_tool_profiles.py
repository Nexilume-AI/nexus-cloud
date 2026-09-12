"""Actual personal profile persistence and Router capability, no remote runner."""
from datetime import timedelta
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import exceptions
from apps.common.subjects import request_subject
from apps.workspaces.models import WorkspaceConnection, WorkspaceTerminalSession, ComputerRuntimeDevice, WorkspaceToolManagedProfile
from apps.workspaces.connection_core import WorkspaceConfigConflict
from nexus_personal.models import PersonalRouterCredential
from nexus_personal import tool_profiles
from .provider_http_fixture import ProviderHTTPFixture
from . import test_router_runtime as runtime_tests


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1")
class PersonalToolProfileTests(ProviderHTTPFixture, TestCase):
    source = runtime_tests.PersonalRouterRuntimeTests.source
    runtime = runtime_tests.PersonalRouterRuntimeTests.runtime
    post = runtime_tests.PersonalRouterRuntimeTests.post

    def setUp(self):
        runtime_tests.PersonalRouterRuntimeTests.setUp(self)
        request = self.request()
        from nexus_personal.authentication import validate_owner
        validate_owner(request, request.user)
        self.computer = WorkspaceConnection.objects.create(tenant=self.installation.tenant,
            project=self.installation.project, created_by=request.user, name="Tool profile computer",
            connection_type="runtime", owner_subject_type="user", owner_subject_hash=request_subject(request).subject_hash)
        self.device = ComputerRuntimeDevice.objects.create(connection=self.computer,
            public_key_pem="model-test-not-enrolled", public_key_fingerprint="a" * 64, platform="linux")
        self.session = WorkspaceTerminalSession.objects.create(tenant=self.installation.tenant,
            project=self.installation.project, connection=self.computer, created_by=request.user, status="active")

    def issue(self):
        response = self.post(f"/api/v1/routers/{self.router.pk}/export-credentials/", {})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def profile(self, create=True):
        return tool_profiles.get_profile(request=self.request(), session_id=self.session.pk, create=create)

    def bind(self, credential_id, expected="", applied="a" * 64):
        return tool_profiles.bind_router_credential(request=self.request(), session_id=self.session.pk,
            credential_id=credential_id, expected_revision=expected, applied_revision=applied)

    def state(self, token):
        return tool_profiles.router_credential_state(request=self.request(), session_id=self.session.pk, remote_token=token)

    def test_profile_reuses_real_router_credential_and_preserves_agent_metadata(self):
        profile = self.profile()
        profile.managed_mcp_servers = {"keep-agent": {"agent_id": "metadata-only"}}
        profile.save()
        data = self.issue()
        bound = self.bind(data["gateway_api_key_id"])
        self.assertEqual(self.state(data["gateway_api_key"]), "ready")
        self.assertEqual(bound.pk, profile.pk)
        self.assertEqual(bound.managed_mcp_servers, profile.managed_mcp_servers)
        self.assertEqual(bound.router_id, self.router.pk)
        self.assertNotIn(data["gateway_api_key"], str(WorkspaceToolManagedProfile.objects.values().get()))
        with self.assertRaises(WorkspaceConfigConflict):
            self.bind(data["gateway_api_key_id"], expected="stale")
        self.assertEqual(self.profile().config_revision, "a" * 64)
        self.assertEqual(PersonalRouterCredential.objects.count(), 1)
        from rest_framework.test import APIClient
        limited = APIClient()
        limited.credentials(HTTP_AUTHORIZATION="Bearer " + data["gateway_api_key"])
        response = limited.post("/api/v1/openai/v1/chat/completions", {"model": self.model,
            "messages": [{"role": "user", "content": "tool profile invocation"}]},
            format="json", HTTP_X_REQUEST_ID="tool-profile-request")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(self.calls), 1)

    def test_unavailable_computer_or_changed_profile_ownership_cannot_be_reused(self):
        with self.assertRaises(exceptions.APIException):
            tool_profiles.get_profile(request=self.request(self.other), session_id=self.session.pk, create=True)
        profile = self.profile()
        WorkspaceToolManagedProfile.objects.filter(pk=profile.pk).update(created_by=self.other)
        with self.assertRaises(exceptions.APIException):
            self.profile()
        WorkspaceToolManagedProfile.objects.filter(pk=profile.pk).update(created_by=self.installation.owner)
        self.device.revoked_at = timezone.now()
        self.device.save()
        with self.assertRaises(exceptions.APIException):
            self.profile()
        self.device.revoked_at = None
        self.device.save()
        self.session.status = "closed"
        self.session.save()
        with self.assertRaises(exceptions.APIException):
            self.profile()

    def test_changed_credential_owner_is_rejected_without_rebinding_or_escalation(self):
        data = self.issue()
        original = self.bind(data["gateway_api_key_id"])
        PersonalRouterCredential.objects.filter(pk=data["gateway_api_key_id"]).update(owner=self.other)
        self.assertEqual(self.state(data["gateway_api_key"]), "needs_repair")
        with self.assertRaises(exceptions.APIException):
            self.bind(data["gateway_api_key_id"], expected="a" * 64, applied="b" * 64)
        original.refresh_from_db()
        self.assertEqual(original.config_revision, "a" * 64)
        self.assertEqual(original.router_id, self.router.pk)

    def test_expired_revoked_or_wrong_token_needs_repair_without_issuing_new_keys(self):
        self.assertEqual(self.state(""), "not_configured")
        data = self.issue()
        self.bind(data["gateway_api_key_id"])
        self.assertEqual(self.state("wrong"), "needs_repair")
        PersonalRouterCredential.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.state(data["gateway_api_key"]), "needs_repair")
        with self.assertRaises(exceptions.APIException):
            self.bind(data["gateway_api_key_id"], expected="a" * 64)
        PersonalRouterCredential.objects.update(expires_at=timezone.now() + timedelta(days=1), revoked_at=timezone.now())
        self.assertEqual(self.state(data["gateway_api_key"]), "needs_repair")
        self.assertEqual(PersonalRouterCredential.objects.count(), 1)

    def test_profile_unique_constraint_and_soft_delete_allow_recreation(self):
        profile = self.profile()
        self.assertEqual(self.profile().pk, profile.pk)
        with self.assertRaises(IntegrityError), transaction.atomic():
            WorkspaceToolManagedProfile.objects.create(tenant=profile.tenant, project=profile.project,
                connection=profile.connection, created_by=profile.created_by)
        profile.delete()
        self.assertNotEqual(self.profile().pk, profile.pk)
