from datetime import timedelta
import tempfile
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory

from apps.agents import browser_broker
from apps.agents.browser_broker import (
    AttachedBrowserActionFailed,
    AttachedBrowserError,
    AttachedBrowserPermissionRequired,
    _read_browser_launch_details,
    _safe_http_url,
    _store_observation,
    browser_delegate_operation,
    get_browser_delegate_run,
)
from apps.agents.models import (
    Agent,
    AgentBrowserSession,
    AgentComputerBinding,
    AgentDisplayRun,
)
from apps.common.subjects import hash_token, request_subject
from apps.tenancy.models import Membership, Tenant
from apps.workspaces.models import WorkspaceConnection


class AttachedComputerBrowserBrokerTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.caller = user_model.objects.create_user(username="attached-browser-caller")
        self.developer = user_model.objects.create_user(username="attached-browser-developer")
        self.consumer = Tenant.objects.create(name="Browser Consumer", slug="browser-consumer")
        self.producer = Tenant.objects.create(name="Browser Producer", slug="browser-producer")
        Membership.objects.create(tenant=self.consumer, user=self.caller, role=Membership.ROLE_OWNER)
        Membership.objects.create(tenant=self.producer, user=self.developer, role=Membership.ROLE_OWNER)
        self.agent = Agent.objects.create(
            tenant=self.producer,
            name="Attached Browser",
            visibility=Agent.VISIBILITY_PUBLIC,
            publication_status=Agent.PUBLICATION_PUBLISHED,
            status=Agent.STATUS_ACTIVE,
            computer_requirement=Agent.COMPUTER_REQUIRED,
            workspace_capabilities=["browser.control"],
            created_by=self.developer,
        )
        request = APIRequestFactory().get("/")
        request.user = self.caller
        request.tenant_id = str(self.consumer.id)
        subject = request_subject(request)
        connection = WorkspaceConnection.objects.create(
            tenant=self.consumer,
            owner_subject_type=subject.principal_type,
            owner_subject_hash=subject.subject_hash,
            workspace_root="~/.nexus",
            name="Caller Windows Computer",
            ssh_host="127.0.0.1",
            ssh_user="nexus-browser",
            auth_mode=WorkspaceConnection.AUTH_PASSWORD,
            created_by=self.caller,
            metadata={
                "last_facts": {
                    "os": "windows",
                    "browser_available": True,
                    "browser_name": "chrome",
                }
            },
        )
        binding = AgentComputerBinding.objects.create(
            tenant=self.consumer,
            agent=self.agent,
            connection=connection,
            caller_subject_hash=subject.subject_hash,
            caller_principal_type=subject.principal_type,
            is_default=True,
        )
        self.token = "browser-delegate-token"
        self.run = AgentDisplayRun.objects.create(
            tenant=self.producer,
            consumer_tenant=self.consumer,
            agent=self.agent,
            run_kind=AgentDisplayRun.KIND_INVOCATION,
            caller_principal_type=subject.principal_type,
            caller_principal_id=subject.principal_id,
            caller_subject_hash=subject.subject_hash,
            computer_binding=binding,
            status=AgentDisplayRun.STATUS_RUNNING,
            write_token="run-write-token",
            workspace_capabilities_snapshot=["browser.control"],
            browser_delegate_token_hash=hash_token(self.token),
            browser_delegate_token_expires_at=timezone.now() + timedelta(hours=1),
        )
        self.media = tempfile.TemporaryDirectory()
        self.media_settings = override_settings(MEDIA_ROOT=self.media.name)
        self.media_settings.enable()
        self.addCleanup(self.media_settings.disable)
        self.addCleanup(self.media.cleanup)
        self.addCleanup(browser_broker._WORKERS.clear)

    def _other_run(self, *, status: str) -> AgentDisplayRun:
        return AgentDisplayRun.objects.create(
            tenant=self.producer,
            consumer_tenant=self.consumer,
            agent=self.agent,
            run_kind=AgentDisplayRun.KIND_INVOCATION,
            caller_principal_type=self.run.caller_principal_type,
            caller_principal_id=self.run.caller_principal_id,
            caller_subject_hash=self.run.caller_subject_hash,
            computer_binding=self.run.computer_binding,
            status=status,
            write_token="other-browser-run-token",
            workspace_capabilities_snapshot=["browser.control"],
        )

    @override_settings(NEXUS_ATTACHED_BROWSER_MAX_PER_COMPUTER=2)
    def test_capacity_evicts_oldest_completed_browser_worker(self):
        older_run = self._other_run(status=AgentDisplayRun.STATUS_COMPLETED)
        newer_run = self._other_run(status=AgentDisplayRun.STATUS_COMPLETED)
        older_session = AgentBrowserSession.objects.create(
            run=older_run,
            connection=self.run.computer_binding.connection,
            status=AgentBrowserSession.STATUS_ACTIVE,
        )
        newer_session = AgentBrowserSession.objects.create(
            run=newer_run,
            connection=self.run.computer_binding.connection,
            status=AgentBrowserSession.STATUS_ACTIVE,
        )
        now = timezone.now()
        AgentBrowserSession.objects.filter(id=older_session.id).update(last_used_at=now - timedelta(hours=2))
        AgentBrowserSession.objects.filter(id=newer_session.id).update(last_used_at=now - timedelta(hours=1))
        older_worker = mock.Mock()
        older_worker.stop.side_effect = lambda: AgentBrowserSession.objects.filter(id=older_session.id).update(
            status=AgentBrowserSession.STATUS_CLOSED,
            closed_at=timezone.now(),
        )
        newer_worker = mock.Mock()
        created_worker = SimpleNamespace(viewport=(1280, 720))
        browser_broker._WORKERS.update(
            {
                str(older_run.id): older_worker,
                str(newer_run.id): newer_worker,
            }
        )

        with (
            mock.patch.object(browser_broker, "_ensure_reaper"),
            mock.patch.object(browser_broker, "_RemoteBrowserWorker", return_value=created_worker),
        ):
            worker = browser_broker._worker_for_run(run=self.run, viewport=(1280, 720))

        self.assertIs(worker, created_worker)
        older_worker.stop.assert_called_once_with()
        newer_worker.stop.assert_not_called()
        older_session.refresh_from_db()
        self.assertEqual(older_session.status, AgentBrowserSession.STATUS_CLOSED)
        self.assertNotIn(str(older_run.id), browser_broker._WORKERS)
        self.assertIn(str(newer_run.id), browser_broker._WORKERS)

    @override_settings(NEXUS_ATTACHED_BROWSER_MAX_PER_COMPUTER=2)
    def test_capacity_never_evicts_running_browser_workers(self):
        first_run = self._other_run(status=AgentDisplayRun.STATUS_RUNNING)
        second_run = self._other_run(status=AgentDisplayRun.STATUS_RUNNING)
        for other_run in (first_run, second_run):
            AgentBrowserSession.objects.create(
                run=other_run,
                connection=self.run.computer_binding.connection,
                status=AgentBrowserSession.STATUS_ACTIVE,
            )
            browser_broker._WORKERS[str(other_run.id)] = mock.Mock()

        with (
            mock.patch.object(browser_broker, "_ensure_reaper"),
            mock.patch.object(browser_broker, "_RemoteBrowserWorker") as worker_type,
            self.assertRaises(AttachedBrowserError),
        ):
            browser_broker._worker_for_run(run=self.run, viewport=(1280, 720))

        worker_type.assert_not_called()
        for other_run in (first_run, second_run):
            browser_broker._WORKERS[str(other_run.id)].stop.assert_not_called()

    @override_settings(NEXUS_ATTACHED_BROWSER_MAX_PER_COMPUTER=1)
    def test_capacity_reconciles_disabled_legacy_session_after_server_restart(self):
        completed_run = self._other_run(status=AgentDisplayRun.STATUS_COMPLETED)
        stale_session = AgentBrowserSession.objects.create(
            run=completed_run,
            connection=self.run.computer_binding.connection,
            status=AgentBrowserSession.STATUS_ACTIVE,
        )
        created_worker = SimpleNamespace(viewport=(1280, 720))

        with (
            mock.patch.object(browser_broker, "_ensure_reaper"),
            mock.patch.object(browser_broker, "_RemoteBrowserWorker", return_value=created_worker),
        ):
            worker = browser_broker._worker_for_run(run=self.run, viewport=(1280, 720))

        self.assertIs(worker, created_worker)
        stale_session.refresh_from_db()
        self.assertEqual(stale_session.status, AgentBrowserSession.STATUS_FAILED)
        self.assertTrue(stale_session.last_error.startswith("LEGACY_SSH_DISABLED:"))

    def test_persisted_cleanup_stops_only_exact_profile_process_tree_and_retries_removal(self):
        script = browser_broker._remote_browser_cleanup_script(
            remote_pid=421,
            profile_path=r"C:\Users\caller\.nexus\browser-runs\run-1",
        )
        self.assertIn("$profileArgument = '--user-data-dir=' + $profile", script)
        self.assertIn("$_.CommandLine.IndexOf($profileArgument", script)
        self.assertIn("$_.ParentProcessId -eq $parent", script)
        self.assertIn("Stop-Process -Id $processId", script)
        self.assertIn("[DateTime]::UtcNow.AddSeconds(5)", script)
        self.assertIn("Remove-Item -LiteralPath $profile", script)
        self.assertIn("$profile.StartsWith($base", script)
        self.assertNotIn("Get-Process chrome", script)

    def test_linux_launcher_uses_non_root_isolated_loopback_chromium_contract(self):
        script = browser_broker._posix_browser_launch_script(
            platform=AgentBrowserSession.PLATFORM_LINUX,
            profile_path="/home/caller/.nexus/browser-runs/run-1",
        )
        self.assertIn("umask 077", script)
        self.assertIn('id -u', script)
        self.assertIn("Linux browser sessions require a non-root SSH user", script)
        self.assertIn("--remote-debugging-address=127.0.0.1", script)
        self.assertIn("--headless=new", script)
        self.assertNotIn("--no-sandbox", script)
        self.assertLess(script.index("google-chrome-stable"), script.index("chromium"))
        self.assertLess(script.index("chromium"), script.index("microsoft-edge-stable"))

    def test_macos_launcher_discovers_chromium_family_without_safari(self):
        script = browser_broker._posix_browser_launch_script(
            platform=AgentBrowserSession.PLATFORM_MACOS,
            profile_path="/Users/caller/.nexus/browser-runs/run-1",
        )
        self.assertIn("/Applications/Google Chrome.app", script)
        self.assertIn("/Users/caller/Applications/Chromium.app", script)
        self.assertIn("/Applications/Microsoft Edge.app", script)
        self.assertNotIn("Safari", script)
        self.assertNotIn("--no-sandbox", script)

    def test_posix_cleanup_targets_exact_run_profile_and_process_tree(self):
        script = browser_broker._posix_browser_cleanup_script(
            remote_pid=421,
            profile_path="/home/caller/.nexus/browser-runs/run-1",
        )
        self.assertIn('"$HOME/.nexus/browser-runs"', script)
        self.assertIn("--user-data-dir=$profile", script)
        self.assertIn("$2 == wanted", script)
        self.assertIn("kill -TERM", script)
        self.assertIn("kill -KILL", script)
        self.assertIn('rm -rf -- "$profile"', script)
        self.assertNotIn("pkill", script)
        self.assertNotIn("killall", script)

    def test_browser_session_platform_defaults_to_windows_for_legacy_rows(self):
        session = AgentBrowserSession.objects.create(
            run=self._other_run(status=AgentDisplayRun.STATUS_COMPLETED),
            connection=self.run.computer_binding.connection,
        )
        self.assertEqual(session.platform, AgentBrowserSession.PLATFORM_WINDOWS)

    def test_restart_cleanup_delegates_saved_linux_session_to_runtime(self):
        connection = self.run.computer_binding.connection
        connection.connection_type = WorkspaceConnection.TYPE_RUNTIME
        connection.save(update_fields=["connection_type"])
        session = AgentBrowserSession.objects.create(
            run=self._other_run(status=AgentDisplayRun.STATUS_COMPLETED),
            connection=connection,
            platform=AgentBrowserSession.PLATFORM_LINUX,
            status=AgentBrowserSession.STATUS_ACTIVE,
            runtime_session_id="persisted-runtime-browser",
        )
        with (
            mock.patch("apps.workspaces.computer_runtime.execute_runtime_command", return_value={"closed": True}) as execute,
            mock.patch.object(browser_broker, "workspace_runner") as legacy,
        ):
            cleaned = browser_broker._close_persisted_browser_session(session)

        self.assertTrue(cleaned)
        execute.assert_called_once_with(
            connection=connection, operation="browser.close", required_scope="browser.control",
            payload={"browser_session_id": "persisted-runtime-browser", "run_id": str(session.run_id)},
            timeout_seconds=10, display_run_id=str(session.run_id),
        )
        legacy.assert_not_called()
        session.refresh_from_db()
        self.assertEqual(session.status, AgentBrowserSession.STATUS_CLOSED)

    def test_browser_launch_marker_does_not_wait_for_exec_channel_exit(self):
        class PersistentLaunchChannel:
            def __init__(self):
                self.stdout = [b'{"pid":421,"port":9222,"profile":"C:\\\\isolated"}\r\n']
                self.closed = False

            def recv_ready(self):
                return bool(self.stdout)

            def recv(self, _size):
                return self.stdout.pop(0)

            def recv_stderr_ready(self):
                return False

            def recv_stderr(self, _size):
                return b""

            def exit_status_ready(self):
                return False

        channel = PersistentLaunchChannel()
        details = _read_browser_launch_details(channel, timeout_seconds=1)
        self.assertEqual(details["pid"], 421)
        self.assertEqual(details["port"], 9222)
        self.assertFalse(channel.closed)

    def test_browser_launcher_failure_reports_remote_error(self):
        class FailedLaunchChannel:
            def __init__(self):
                self.stderr = [b"Chrome or Edge is not installed"]

            def recv_ready(self):
                return False

            def recv(self, _size):
                return b""

            def recv_stderr_ready(self):
                return bool(self.stderr)

            def recv_stderr(self, _size):
                return self.stderr.pop(0)

            def exit_status_ready(self):
                return True

            def recv_exit_status(self):
                return 1

        with self.assertRaises(AttachedBrowserError) as raised:
            _read_browser_launch_details(FailedLaunchChannel(), timeout_seconds=1)
        self.assertIn("Chrome or Edge is not installed", str(raised.exception.detail))

    def test_delegate_is_run_token_computer_and_scope_bound(self):
        self.assertEqual(
            get_browser_delegate_run(run_id=str(self.run.id), token=self.token).id,
            self.run.id,
        )
        with self.assertRaises(AttachedBrowserPermissionRequired):
            get_browser_delegate_run(run_id=str(self.run.id), token="wrong")
        self.run.workspace_capabilities_snapshot = []
        self.run.save(update_fields=["workspace_capabilities_snapshot", "updated_at"])
        with self.assertRaises(AttachedBrowserPermissionRequired):
            get_browser_delegate_run(run_id=str(self.run.id), token=self.token)

    def test_internal_endpoint_uses_only_browser_delegate_token(self):
        client = APIClient()
        path = f"/api/v1/internal/agent-runs/{self.run.id}/browser/"
        denied = client.post(
            path,
            {"operation": "observe", "viewport": [1280, 720]},
            format="json",
            HTTP_AUTHORIZATION="Bearer " + self.token,
        )
        self.assertEqual(denied.status_code, 403, denied.content)
        self.assertEqual(denied.json()["error"]["code"], "BROWSER_PERMISSION_REQUIRED")
        with mock.patch(
            "apps.agents.callback_views.browser_delegate_operation",
            return_value={"revision": 1},
        ) as operation:
            response = client.post(
                path,
                {"operation": "observe", "viewport": [1280, 720]},
                format="json",
                HTTP_X_NEXUS_BROWSER_DELEGATE_TOKEN=self.token,
            )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"]["revision"], 1)
        operation.assert_called_once()
        self.assertEqual(operation.call_args.kwargs["token"], self.token)

    def test_runtime_browser_forwards_the_managed_operation_key(self):
        session = SimpleNamespace(runtime_session_id="browser-session")
        runtime_result = {
            "image_base64": "",
            "content_type": "image/jpeg",
            "dom": {"nodes": []},
            "revision": 3,
        }
        with (
            mock.patch("apps.agents.browser_runtime._runtime_browser_session", return_value=session),
            mock.patch("apps.workspaces.computer_runtime.execute_runtime_command", return_value=runtime_result) as execute,
            mock.patch("apps.agents.browser_runtime._store_runtime_observation", return_value={"revision": 3}),
        ):
            result = browser_delegate_operation(
                run_id=str(self.run.id),
                token=self.token,
                data={
                    "operation": "observe",
                    "viewport": [1280, 720],
                    "idempotency_key": "nxo_browser_action",
                },
            )

        self.assertEqual(result["revision"], 3)
        self.assertEqual(execute.call_args.kwargs["idempotency_key"], "nxo_browser_action")

    def _runtime_session(self):
        connection = self.run.computer_binding.connection
        connection.connection_type = "runtime"
        connection.save(update_fields=["connection_type"])
        return AgentBrowserSession.objects.create(
            run=self.run, connection=connection, status=AgentBrowserSession.STATUS_ACTIVE,
            runtime_session_id="runtime-browser-fixture",
        )

    def test_non_web_urls_and_runtime_action_rejections_do_not_fall_back_to_ssh(self):
        for value in ("file:///etc/passwd", "javascript:alert(1)", "chrome://settings"):
            with self.assertRaises(AttachedBrowserActionFailed):
                _safe_http_url(value)

        session = self._runtime_session()
        with (
            mock.patch("apps.agents.browser_runtime._runtime_browser_session", return_value=session),
            mock.patch("apps.workspaces.computer_runtime.execute_runtime_command",
                       side_effect=AttachedBrowserActionFailed("Coordinates are outside the viewport.")) as execute,
            mock.patch("apps.agents.browser_broker._worker_for_run") as legacy,
        ):
            with self.assertRaises(AttachedBrowserActionFailed):
                browser_delegate_operation(
                    run_id=str(self.run.id),
                    token=self.token,
                    data={
                        "operation": "action",
                        "viewport": [1280, 720],
                        "action": {
                            "kind": "click",
                            "expected_revision": 1,
                            "parameters": {"x": 1280, "y": 10},
                        },
                    },
                )
        legacy.assert_not_called()
        self.assertEqual(execute.call_args.kwargs["operation"], "browser.action")
        self.assertEqual(execute.call_args.kwargs["required_scope"], "browser.control")
        self.assertEqual(execute.call_args.kwargs["payload"]["action"]["parameters"], {"x": 1280, "y": 10})

    def test_sensitive_fill_value_is_not_returned_in_an_error(self):
        session = self._runtime_session()
        with (
            mock.patch("apps.agents.browser_runtime._runtime_browser_session", return_value=session),
            mock.patch("apps.workspaces.computer_runtime.execute_runtime_command",
                       side_effect=RuntimeError("could not fill password=super-secret")) as execute,
            mock.patch("apps.agents.browser_broker._worker_for_run") as legacy,
        ):
            with self.assertRaises(AttachedBrowserActionFailed) as raised:
                browser_delegate_operation(
                    run_id=str(self.run.id),
                    token=self.token,
                    data={
                        "operation": "action",
                        "viewport": [1280, 720],
                        "action": {
                            "kind": "fill",
                            "expected_revision": 1,
                            "parameters": {
                                "target": "e1",
                                "is_ref": True,
                                "value": "super-secret",
                            },
                        },
                    },
                )
        self.assertNotIn("super-secret", str(raised.exception.detail))
        self.assertEqual(execute.call_count, 1)
        legacy.assert_not_called()

    def test_observation_is_persisted_after_runtime_command_returns(self):
        session = self._runtime_session()
        events = []
        capture = {
            "image": b"jpeg-frame",
            "dom": {"nodes": [], "truncated": False},
            "url": "https://example.test/",
            "title": "Example",
            "revision": 1,
        }

        def execute(**_kwargs):
            events.append("runtime_result")
            return capture

        def persist(**kwargs):
            self.assertEqual(events, ["runtime_result"])
            self.assertIs(kwargs["result"], capture)
            events.append("persist")
            return {"revision": 1}

        with (
            mock.patch("apps.agents.browser_runtime._runtime_browser_session", return_value=session),
            mock.patch("apps.workspaces.computer_runtime.execute_runtime_command", side_effect=execute),
            mock.patch("apps.agents.browser_runtime._store_runtime_observation", side_effect=persist) as store,
            mock.patch("apps.agents.browser_broker._worker_for_run") as legacy,
        ):
            result = browser_delegate_operation(
                run_id=str(self.run.id),
                token=self.token,
                data={"operation": "observe", "viewport": [1280, 720]},
            )

        self.assertEqual(result["revision"], 1)
        store.assert_called_once()
        self.assertEqual(events, ["runtime_result", "persist"])
        legacy.assert_not_called()

    def test_observation_publishes_protected_attached_computer_frame(self):
        page = SimpleNamespace(
            url="https://example.test/private?token=secret",
            screenshot=mock.Mock(return_value=b"jpeg-frame"),
            evaluate=mock.Mock(return_value={"nodes": [{"ref": "e1"}], "truncated": False}),
            title=mock.Mock(return_value="Attached page"),
        )
        worker = SimpleNamespace(
            page=page,
            viewport=(1280, 720),
            revision=0,
            session_id="00000000-0000-0000-0000-000000000000",
        )
        value = _store_observation(run_id=str(self.run.id), worker=worker, action="observe")
        self.assertTrue(value["frame_published"])
        self.assertEqual(value["computer_name"], "Caller Windows Computer")
        event = self.run.events.get(event_type="CUSTOM")
        frame = event.payload_json["value"]
        self.assertEqual(frame["source"], "attached_computer")
        self.assertEqual(frame["profile"], "isolated")
        self.assertNotIn("secret", frame["url"])
