"""Read-only delegates must not implicitly create remote directories."""
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from apps.agents.runtime_services import list_invocation_workspace_files


class WorkspaceListReadOnlyTests(SimpleTestCase):
    def test_list_never_initializes_remote_directory(self):
        run = SimpleNamespace(id="run", caller_subject_hash="caller",
            computer_binding=SimpleNamespace(connection=object()),
            workspace_root="/authorized/workspace", output_root="/authorized/outputs",
            workspace_cwd="selected")
        for root_kind, relative_path in (("workspace", "selected"), ("output", ".")):
            with self.subTest(root_kind=root_kind), \
                    mock.patch("apps.agents.runtime_services.get_workspace_delegate_run", return_value=run) as authorize, \
                    mock.patch("apps.workspaces.execution.ensure_workspace_directory") as create, \
                    mock.patch("apps.workspaces.execution.list_workspace_files", return_value={"entries": []}) as listing:
                result = list_invocation_workspace_files(run_id="run", token="test", path=".", root_kind=root_kind)
                authorize.assert_called_once_with(run_id="run", token="test", scope="files.list")
                create.assert_not_called()
                self.assertEqual(listing.call_args.kwargs["path"], relative_path)
                self.assertEqual(result, {"entries": []})

    def test_missing_directory_does_not_trigger_write_fallback(self):
        run = SimpleNamespace(id="run", caller_subject_hash="caller",
            computer_binding=SimpleNamespace(connection=object()),
            workspace_root="/authorized/missing", output_root="/authorized/outputs", workspace_cwd=".")
        with mock.patch("apps.agents.runtime_services.get_workspace_delegate_run", return_value=run), \
                mock.patch("apps.workspaces.execution.ensure_workspace_directory") as create, \
                mock.patch("apps.workspaces.execution.list_workspace_files", side_effect=FileNotFoundError):
            with self.assertRaises(FileNotFoundError):
                list_invocation_workspace_files(run_id="run", token="test", path=".")
            create.assert_not_called()
