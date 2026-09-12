"""Original operational Provider HTTP guards; hosts decode their success payload.

Both hosts issue real authenticated requests. Credential verification/process
mocks are the original boundaries, not a live upstream acceptance claim.
"""
from unittest.mock import Mock, patch
from apps.common.crypto import decrypt_secret
from apps.common.models import SoftDeleteModel
from apps.gateway.provider_adapters import ProviderClientError
from apps.providers.models import ProviderAccount, ProviderRuntimeAccount


class ProviderConnectionHTTPGuards:
    def test_create_and_list_return_one_user_facing_provider(self):
        created = self.create_connection()

        response = self.request("get", "/api/v1/provider-connections/")

        self.assertEqual(response.status_code, 200, response.content)
        providers = self.connection_list(response)
        self.assertEqual(len(providers), 1)
        self.assertEqual(providers[0]["id"], created["id"])
        self.assertEqual(providers[0]["name"], "Production OpenAI")
        self.assertEqual(providers[0]["engine"], "direct_api")
        self.assertEqual(providers[0]["upstream_provider"], "openai-compatible")
        self.assertNotIn("key", providers[0])
        account = ProviderAccount.objects.get(id=created["id"])
        self.assertEqual(account.source_runtime_accounts.exclude(status="deleted").count(), 1)

    def test_running_provider_rejects_engine_change_without_partial_update(self):
        created = self.create_connection()
        account = ProviderAccount.objects.get(id=created["id"])
        runtime = account.source_runtime_accounts.get()
        runtime.status = ProviderRuntimeAccount.STATUS_ACTIVE
        runtime.save(update_fields=["status", "updated_at"])

        response = self.request(
            "patch",
            f"/api/v1/provider-connections/{account.id}/",
            {"engine": "cliproxyapi", "upstream_provider": "claude"},
        )

        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["error"]["code"], "PROVIDER_MUST_BE_STOPPED")
        account.refresh_from_db()
        runtime.refresh_from_db()
        self.assertEqual(account.provider.name, "openai-compatible")
        self.assertEqual(runtime.runtime_type, ProviderRuntimeAccount.RUNTIME_DIRECT_API)

    @patch("apps.providers.connection_services.verify_openai_compatible_credentials")
    def test_direct_api_credential_rotation_verifies_before_commit(self, verify_credentials):
        created = self.create_connection()
        account = ProviderAccount.objects.get(id=created["id"])

        response = self.request(
            "patch",
            f"/api/v1/provider-connections/{account.id}/",
            {"url": "https://new-provider.example.test/v1", "key": "new-secret"},
        )

        self.assertEqual(response.status_code, 200, response.content)
        verify_credentials.assert_called_once_with(
            base_url="https://new-provider.example.test/v1",
            api_key="new-secret",
            provider_name="openai-compatible",
        )
        account.refresh_from_db()
        self.assertEqual(account.url, "https://new-provider.example.test/v1")
        self.assertEqual(decrypt_secret(account.encrypted_key), "new-secret")

    @patch("apps.providers.connection_services.verify_openai_compatible_credentials")
    def test_failed_credential_rotation_preserves_existing_connection(self, verify_credentials):
        created = self.create_connection()
        account = ProviderAccount.objects.get(id=created["id"])
        original_url = account.url
        original_secret = account.encrypted_key
        verify_credentials.side_effect = ProviderClientError("rejected")

        response = self.request(
            "patch",
            f"/api/v1/provider-connections/{account.id}/",
            {"url": "https://bad-provider.example.test/v1", "key": "bad-secret"},
        )

        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["error"]["code"], "PROVIDER_CREDENTIAL_VERIFICATION_FAILED")
        account.refresh_from_db()
        self.assertEqual(account.url, original_url)
        self.assertEqual(account.encrypted_key, original_secret)

    @patch("apps.providers.runtime_services.get_provider_runtime_runner")
    def test_repeated_start_and_stop_are_idempotent_during_transition(self, get_runner):
        created = self.create_connection(engine="codex_proxy", upstream="openai")
        account = ProviderAccount.objects.get(id=created["id"])
        runtime = account.source_runtime_accounts.get()
        runtime.status = ProviderRuntimeAccount.STATUS_STARTING
        runtime.save(update_fields=["status", "updated_at"])

        start = self.request("post", f"/api/v1/provider-connections/{account.id}/start/")
        self.assertEqual(start.status_code, 200, start.content)
        get_runner.assert_not_called()

        blocked_stop = self.request("post", f"/api/v1/provider-connections/{account.id}/stop/")
        self.assertEqual(blocked_stop.status_code, 409, blocked_stop.content)
        self.assertEqual(blocked_stop.json()["error"]["code"], "PROVIDER_RUNTIME_BUSY")

        runtime.status = ProviderRuntimeAccount.STATUS_STOPPING
        runtime.save(update_fields=["status", "updated_at"])
        repeated_stop = self.request("post", f"/api/v1/provider-connections/{account.id}/stop/")
        self.assertEqual(repeated_stop.status_code, 200, repeated_stop.content)
        get_runner.assert_not_called()

    def test_runner_stop_failure_keeps_provider_and_dependencies(self):
        created = self.create_connection(name="Claude Browser", engine="cliproxyapi", upstream="claude")
        account = ProviderAccount.objects.get(id=created["id"])
        runtime = account.source_runtime_accounts.get()
        runtime.status = ProviderRuntimeAccount.STATUS_ACTIVE
        runtime.save(update_fields=["status", "updated_at"])
        runner = Mock()
        runner.stop.side_effect = RuntimeError("runner refused stop")

        with patch("apps.providers.connection_services.get_provider_runtime_runner", return_value=runner):
            response = self.request(
                "post",
                f"/api/v1/provider-connections/{account.id}/remove/",
                {"confirmation_name": "Claude Browser"},
            )

        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["error"]["code"], "PROVIDER_RUNTIME_STOP_FAILED")
        account.refresh_from_db()
        runtime.refresh_from_db()
        self.assertNotEqual(account.status, SoftDeleteModel.STATUS_DELETED)
        self.assertEqual(runtime.status, ProviderRuntimeAccount.STATUS_ACTIVE)
