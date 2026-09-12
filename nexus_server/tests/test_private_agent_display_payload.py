from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from apps.agents.services import private_display_payload


class _EmptyRelated:
    def order_by(self, *_args):
        return self

    def filter(self, **_kwargs):
        return self

    def first(self):
        return None

    def values(self, *_args):
        return []

    def values_list(self, *_args, **_kwargs):
        return self

    def aggregate(self, **kwargs):
        return {key: 0 for key in kwargs}

    def __getitem__(self, _key):
        return []


class _Run(SimpleNamespace):
    def __init__(self, **kwargs):
        super().__init__(runtime_invocations=_EmptyRelated(), model_usage=_EmptyRelated(),
            computer_attachments=_EmptyRelated(), project_context_snapshot={},
            computer_revision=0, workspace_cwd=".", **kwargs)


class PrivateAgentDisplayPayloadTests(SimpleTestCase):
    def setUp(self):
        # Follow-up persistence has its own database/integration test matrix;
        # these payload-shape tests deliberately use non-model Run doubles.
        patcher = mock.patch("apps.agents.follow_ups.payload", return_value={"mode": "none", "turn_index": 1, "items": []})
        patcher.start()
        self.addCleanup(patcher.stop)

    @mock.patch("apps.agents.services.public_display_run", return_value={"id": "run-1", "status": "running"})
    @mock.patch("apps.agents.services.interaction_tools")
    def test_task_run_exposes_stable_interactor_metadata(self, interaction_tools, _public_run) -> None:
        interaction_tools.return_value = [{
            "name": "demo.echo",
            "display_mode": "task",
            "policy": {"task": True, "continuable": True, "chat": False, "interactive": False},
        }]
        task = SimpleNamespace(
            tool_name="demo.echo",
            status="input_required",
            continuable=True,
            recovery_protocol=1,
            operations=_EmptyRelated(),
            retry_count=1,
            error_code="",
            result_json={"echo": "hello"},
            expires_at="2026-08-16T03:00:00Z",
        )
        run = _Run(
            agent=SimpleNamespace(name="OpenWrt Echo"),
            runtime=SimpleNamespace(),
            execution_task=task,
            interactions=_EmptyRelated(),
            messages=_EmptyRelated(),
            display_title="",
            display_title_source="pending",
            interaction_mode="task",
            title="OpenWrt Echo: demo.echo",
            computer_binding_id=None,
            mobile_binding_id=None,
            mobile_capabilities_snapshot=[],
            mobile_commands=_EmptyRelated(),
        )

        payload = private_display_payload(run=run)

        self.assertEqual(payload["agent_name"], "OpenWrt Echo")
        self.assertEqual(payload["tool_name"], "demo.echo")
        self.assertEqual(payload["display_mode"], "task")
        self.assertTrue(payload["tool_policy"]["continuable"])
        self.assertEqual(payload["execution_task"]["status"], "input_required")
        self.assertEqual(payload["execution_task"]["result"], {"echo": "hello"})
        self.assertIsNone(payload["failure"])
        run.status, task.error_code = "failed", "BROWSER_SESSION_LOST"
        failed = private_display_payload(run=run)
        self.assertEqual(failed["failure"]["domain"], "browser")
        self.assertIn("new_run", failed["failure"]["actions"])
        run.status = "running"
        self.assertIsNone(private_display_payload(run=run)["failure"])

    @mock.patch("apps.agents.services.public_display_run", return_value={"id": "run-2", "status": "running"})
    @mock.patch("apps.agents.services.interaction_tools")
    def test_chat_metadata_does_not_turn_a_run_into_a_thread(self, interaction_tools, _public_run) -> None:
        interaction_tools.return_value = [{
            "name": "assistant.chat",
            "display_mode": "chat",
            "policy": {"task": True, "continuable": True, "chat": True, "interactive": True},
        }]
        run = _Run(
            agent=SimpleNamespace(name="OpenWrt Assistant"),
            runtime=SimpleNamespace(),
            interactions=_EmptyRelated(),
            messages=_EmptyRelated(),
            display_title="",
            display_title_source="pending",
            interaction_mode="stream",
            title="OpenWrt Assistant: assistant.chat",
            computer_binding_id=None,
            mobile_binding_id=None,
            mobile_capabilities_snapshot=[],
            mobile_commands=_EmptyRelated(),
        )

        payload = private_display_payload(run=run)

        self.assertEqual(payload["tool_name"], "assistant.chat")
        self.assertEqual(payload["display_mode"], "tool")
        self.assertIsNone(payload["execution_task"])

    @mock.patch("apps.agents.services.public_display_run", return_value={"id": "run-3", "status": "running"})
    @mock.patch("apps.agents.services.interaction_tools")
    def test_execution_task_is_the_run_display_mode(self, interaction_tools, _public_run) -> None:
        interaction_tools.return_value = [{
            "name": "assistant.chat",
            "display_mode": "chat",
            "policy": {"task": True, "continuable": True, "chat": True, "interactive": True},
        }]
        run = _Run(
            agent=SimpleNamespace(name="OpenWrt Assistant"),
            runtime=SimpleNamespace(),
            execution_task=SimpleNamespace(
                tool_name="assistant.chat",
                status="working",
                continuable=True,
                recovery_protocol=1,
                operations=_EmptyRelated(),
                retry_count=0,
                error_code="",
                result_json={},
                expires_at="2026-08-16T03:00:00Z",
            ),
            interactions=_EmptyRelated(),
            messages=_EmptyRelated(),
            display_title="",
            display_title_source="pending",
            interaction_mode="task",
            title="OpenWrt Assistant: assistant.chat",
            computer_binding_id=None,
            mobile_binding_id=None,
            mobile_capabilities_snapshot=[],
            mobile_commands=_EmptyRelated(),
        )

        payload = private_display_payload(run=run)

        self.assertEqual(payload["display_mode"], "task")
