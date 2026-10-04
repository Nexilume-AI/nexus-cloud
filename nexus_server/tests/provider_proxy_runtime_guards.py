"""Shared Provider proxy contracts; controller mocks are not live Docker acceptance."""
from unittest.mock import MagicMock, patch

from django.test import override_settings

from apps.common.crypto import decrypt_secret, encrypt_secret
from apps.providers.models import Provider, ProviderAccount, ProviderRuntimeAccount
from apps.providers.runtime_runner import (
    CLIProxyAPIRuntimeAdapter, CodexProxyRuntimeAdapter,
    ProviderRuntimeHealthResult, ProviderRuntimeStartResult,
    cliproxyapi_management_key, docker_run_command,
)
from apps.providers.runtime_services import oauth_callback_port_for_runtime, restore_provider_runtime_processes


class ProviderProxyRuntimeGuards:
    def test_codex_proxy_runtime_env_includes_proxy_api_key(self) -> None:
        env = CodexProxyRuntimeAdapter().docker_env(proxy_api_key="nexus-prx-secret")

        self.assertEqual(env["PROXY_API_KEY"], "nexus-prx-secret")

    @override_settings(NEXUS_PROVIDER_RUNTIME_SHARED_IMAGE_TAG="")
    def test_login_runtimes_use_stable_shared_image_when_tag_is_not_configured(self) -> None:
        first_runtime = ProviderRuntimeAccount.objects.create(
            tenant=self.provider_tenant,
            owner=self.provider_owner,
            name="First Login Runtime",
            runtime_type=ProviderRuntimeAccount.RUNTIME_CODEX_PROXY,
        )
        second_runtime = ProviderRuntimeAccount.objects.create(
            tenant=self.provider_tenant,
            owner=self.provider_owner,
            name="Second Login Runtime",
            runtime_type=ProviderRuntimeAccount.RUNTIME_CODEX_PROXY,
        )
        cliproxy_runtime = ProviderRuntimeAccount.objects.create(
            tenant=self.provider_tenant,
            owner=self.provider_owner,
            name="CLIProxy Login Runtime",
            runtime_type=ProviderRuntimeAccount.RUNTIME_CLIPROXYAPI,
        )

        first_image = CodexProxyRuntimeAdapter().image_name(runtime=first_runtime)
        second_image = CodexProxyRuntimeAdapter().image_name(runtime=second_runtime)
        cliproxy_image = CLIProxyAPIRuntimeAdapter().image_name(runtime=cliproxy_runtime)

        self.assertEqual(first_image, "nexus-codex-proxy:runtime")
        self.assertEqual(second_image, first_image)
        self.assertEqual(cliproxy_image, "nexus-cliproxyapi:runtime")

    def test_codex_proxy_docker_command_does_not_expose_oauth_callback_port(self) -> None:
        runtime = ProviderRuntimeAccount.objects.create(
            tenant=self.provider_tenant,
            owner=self.provider_owner,
            name="Codex Docker Runtime",
            runtime_type=ProviderRuntimeAccount.RUNTIME_CODEX_PROXY,
        )

        command = docker_run_command(
            runtime=runtime,
            adapter=CodexProxyRuntimeAdapter(),
            image="nexus-codex-proxy:test",
            proxy_api_key="nexus-prx-secret",
            host_port=19080,
        )

        self.assertIn("127.0.0.1:19080:8080", command)
        self.assertNotIn("127.0.0.1:1455:1455", command)
        self.assertIn("PROXY_API_KEY=nexus-prx-secret", command)
        self.assertIn("--restart", command)
        self.assertIn("unless-stopped", command)
        self.assertIn("--name", command)
        self.assertIn(f"nexus-provider-runtime-{runtime.id}", command)
        self.assertNotIn("--rm", command)

    def test_cliproxyapi_docker_command_does_not_expose_codex_callback_port(self) -> None:
        runtime = ProviderRuntimeAccount.objects.create(
            tenant=self.provider_tenant,
            owner=self.provider_owner,
            name="CLIProxy Docker Runtime",
            runtime_type=ProviderRuntimeAccount.RUNTIME_CLIPROXYAPI,
        )

        command = docker_run_command(
            runtime=runtime,
            adapter=CLIProxyAPIRuntimeAdapter(),
            image="nexus-cliproxyapi:test",
            proxy_api_key="nexus-prx-secret",
            host_port=19081,
        )

        self.assertIn("127.0.0.1:19081:8317", command)
        self.assertNotIn("127.0.0.1:1455:1455", command)
        self.assertIn("MANAGEMENT_PASSWORD=mgmt-nexus-prx-secret", command)

    def test_cliproxyapi_runtime_env_includes_management_password(self) -> None:
        env = CLIProxyAPIRuntimeAdapter().docker_env(proxy_api_key="nexus-prx-secret")

        self.assertEqual(env["MANAGEMENT_PASSWORD"], "mgmt-nexus-prx-secret")
        self.assertEqual(cliproxyapi_management_key(proxy_api_key="nexus-prx-secret"), "mgmt-nexus-prx-secret")

    @patch("apps.providers.runtime_services.refresh_runtime_model_offers")
    @patch("apps.providers.runtime_services.get_provider_runtime_runner")
    def test_cloud_start_restores_expected_provider_runtime_without_rotating_credential(
        self,
        get_runner: MagicMock,
        refresh_models: MagicMock,
    ) -> None:
        runtime = ProviderRuntimeAccount.objects.create(
            tenant=self.provider_tenant,
            owner=self.provider_owner,
            name="Recoverable Provider Runtime",
            runtime_type=ProviderRuntimeAccount.RUNTIME_CLIPROXYAPI,
            status=ProviderRuntimeAccount.STATUS_ACTIVE,
            container_id="old-container",
            internal_api_url="http://127.0.0.1:19081/v1",
            encrypted_proxy_api_key=encrypt_secret("stable-provider-key"),
        )
        runner = get_runner.return_value
        runner.health_check.side_effect = [
            ProviderRuntimeHealthResult(False, False, "container is stopped"),
            ProviderRuntimeHealthResult(True, False, "runtime returned HTTP 200"),
        ]
        runner.restore.return_value = ProviderRuntimeStartResult(
            container_id="restored-container",
            internal_login_url="http://127.0.0.1:29081/v0/management",
            internal_api_url="http://127.0.0.1:29081/v1",
        )

        result = restore_provider_runtime_processes()

        runtime.refresh_from_db()
        self.assertEqual(result, {"examined": 1, "restored": 1, "already_running": 0, "failed": 0})
        self.assertEqual(runtime.status, ProviderRuntimeAccount.STATUS_ACTIVE)
        self.assertEqual(runtime.container_id, "restored-container")
        self.assertEqual(runtime.internal_api_url, "http://127.0.0.1:29081/v1")
        runner.restore.assert_called_once_with(runtime=runtime, proxy_api_key="stable-provider-key")
        refresh_models.assert_called_once_with(runtime=runtime, actor=self.provider_owner, strict=True, schedule_retry=False, bounded_probes=True)

    @patch("apps.providers.runtime_services.get_provider_runtime_runner")
    def test_cloud_start_does_not_restore_provider_stopped_by_user(self, get_runner: MagicMock) -> None:
        ProviderRuntimeAccount.objects.create(
            tenant=self.provider_tenant,
            owner=self.provider_owner,
            name="Stopped Provider Runtime",
            runtime_type=ProviderRuntimeAccount.RUNTIME_CLIPROXYAPI,
            status=ProviderRuntimeAccount.STATUS_STOPPED,
            encrypted_proxy_api_key=encrypt_secret("stable-provider-key"),
        )

        result = restore_provider_runtime_processes()

        self.assertEqual(result, {"examined": 0, "restored": 0, "already_running": 0, "failed": 0})
        get_runner.return_value.restore.assert_not_called()

    @patch("apps.providers.runtime_services.refresh_runtime_model_offers")
    @patch("apps.providers.runtime_services.get_provider_runtime_runner")
    def test_cloud_start_repairs_missing_cloud_owned_proxy_credential(
        self,
        get_runner: MagicMock,
        refresh_models: MagicMock,
    ) -> None:
        runtime = ProviderRuntimeAccount.objects.create(
            tenant=self.provider_tenant,
            owner=self.provider_owner,
            name="Interrupted Provider Runtime",
            runtime_type=ProviderRuntimeAccount.RUNTIME_CODEX_PROXY,
            status=ProviderRuntimeAccount.STATUS_UNHEALTHY,
        )
        runner = get_runner.return_value
        runner.health_check.side_effect = [
            ProviderRuntimeHealthResult(False, False, "container is not running"),
            ProviderRuntimeHealthResult(True, False, "runtime returned HTTP 200"),
        ]
        runner.restore.return_value = ProviderRuntimeStartResult(
            container_id="repaired-container",
            internal_login_url="http://127.0.0.1:39080/auth/login",
            internal_api_url="http://127.0.0.1:39080/v1",
        )

        result = restore_provider_runtime_processes()

        runtime.refresh_from_db()
        self.assertEqual(result, {"examined": 1, "restored": 1, "already_running": 0, "failed": 0})
        repaired_key = decrypt_secret(runtime.encrypted_proxy_api_key)
        self.assertTrue(repaired_key.startswith("nexus-prx-"))
        runner.restore.assert_called_once_with(runtime=runtime, proxy_api_key=repaired_key)
        refresh_models.assert_called_once_with(runtime=runtime, actor=self.provider_owner, strict=True, schedule_retry=False, bounded_probes=True)

    def test_cliproxyapi_browser_login_endpoint_matches_selected_provider(self) -> None:
        openai, _ = Provider.objects.get_or_create(name="openai", defaults={"display_name": "OpenAI"})
        claude, _ = Provider.objects.get_or_create(name="claude", defaults={"display_name": "Claude"})
        openai_account = ProviderAccount.objects.create(
            tenant=self.provider_tenant,
            provider=openai,
            account_id="openai-browser",
            auth_mode=ProviderAccount.AUTH_INTERACTIVE_LOGIN,
            preferred_runtime_type=ProviderAccount.PREFERRED_RUNTIME_CLIPROXYAPI,
        )
        claude_account = ProviderAccount.objects.create(
            tenant=self.provider_tenant,
            provider=claude,
            account_id="claude-browser",
            auth_mode=ProviderAccount.AUTH_INTERACTIVE_LOGIN,
            preferred_runtime_type=ProviderAccount.PREFERRED_RUNTIME_CLIPROXYAPI,
        )
        openai_runtime = ProviderRuntimeAccount(
            tenant=self.provider_tenant,
            source_provider_account=openai_account,
            runtime_type=ProviderRuntimeAccount.RUNTIME_CLIPROXYAPI,
        )
        claude_runtime = ProviderRuntimeAccount(
            tenant=self.provider_tenant,
            source_provider_account=claude_account,
            runtime_type=ProviderRuntimeAccount.RUNTIME_CLIPROXYAPI,
        )

        adapter = CLIProxyAPIRuntimeAdapter()
        self.assertEqual(
            adapter.browser_login_url(runtime=openai_runtime, host_port=19081),
            "http://127.0.0.1:19081/v0/management/codex-auth-url?is_webui=1",
        )
        self.assertEqual(
            adapter.browser_login_url(runtime=claude_runtime, host_port=19082),
            "http://127.0.0.1:19082/v0/management/anthropic-auth-url?is_webui=1",
        )
        self.assertEqual(oauth_callback_port_for_runtime(runtime=openai_runtime), 1455)
        self.assertEqual(oauth_callback_port_for_runtime(runtime=claude_runtime), 54545)
        self.assertEqual(adapter.api_base_url(host_port=19082), "http://127.0.0.1:19082/v1")
