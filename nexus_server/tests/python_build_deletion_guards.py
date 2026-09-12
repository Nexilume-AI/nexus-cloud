"""Original failed-build lifecycle guards; Docker mocks are control-plane checks."""
import json
import tempfile
import uuid
from pathlib import Path
from unittest import mock

from django.test import override_settings
from django.utils import timezone
from apps.agents.models import AgentPythonBuild, AgentRuntimeImage
from apps.agents.python_builds import claim_build, execute_build
from apps.agents.python_build_cleanup import cleanup_deleted_builds
from apps.audit.models import AuditLog
from apps.notifications.models import InboxItem, UserNotification, InboxReceipt, PushDelivery, WebPushSubscription
from apps.notifications.sources import record_build
from tests.python_build_guards import DIGEST


class PythonBuildDeletionFixture:
    def failed(self):
        response = self.upload(secrets='{"MODEL_API_KEY":"private-key-to-delete"}')
        self.assertEqual(response.status_code, 202, response.content)
        build = AgentPythonBuild.objects.get(pk=response.data["id"])
        build.status = "failed"
        build.completed_at = timezone.now()
        build.save()
        return build

    def delete(self, build):
        return self.client.delete(f"{self.url}{build.pk}/", **self.headers)


class PythonBuildDeletionHTTPGuards:
    def test_delete_clears_payloads_inbox_and_preserves_current_image(self):
        image = AgentRuntimeImage.objects.create(agent=self.agent, tenant=self.tenant, image_ref="current:v1")
        self.agent.current_image = image
        self.agent.save()
        build = self.failed()
        item = InboxItem.objects.get(source_type="build", source_id=str(build.pk))
        InboxReceipt.objects.create(item=item, user=self.owner)
        subscription = WebPushSubscription.objects.create(user=self.owner, endpoint_hash="a" * 64, last_seen_at=timezone.now())
        PushDelivery.objects.create(item=item, subscription=subscription)
        with mock.patch("apps.agents.python_build_cleanup.docker") as docker:
            response = self.delete(build)
        self.assertEqual(response.status_code, 200, response.content)
        docker.assert_not_called()
        build.refresh_from_db()
        self.assertEqual(build.status, "deleted")
        for field in ["source", "requirements", "encrypted_secrets", "error_message", "diagnostics", "dependency_lock"]:
            self.assertFalse(getattr(build, field), field)
        self.assertEqual(self.client.get(self.url, **self.headers).data["results"], [])
        self.assertFalse(InboxItem.objects.filter(pk=item.pk).exists())
        self.assertFalse(UserNotification.objects.filter(build=build).exists())
        self.assertFalse(PushDelivery.objects.filter(item_id=item.pk).exists())
        self.assertFalse(InboxReceipt.objects.filter(item_id=item.pk).exists())
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.current_image_id, image.pk)
        audit = AuditLog.objects.get(action="agents.runtime.python.delete_failed")
        self.assertEqual(audit.metadata, {"build_ids": [str(build.pk)], "count": 1})
        self.assertNotIn("private-key-to-delete", json.dumps(audit.metadata))
        self.assertEqual(self.delete(build).status_code, 404)

    def test_stale_reconciler_cannot_recreate_deleted_notification(self):
        stale = self.failed()
        self.delete(stale)
        record_build(stale)
        self.assertFalse(InboxItem.objects.filter(source_type="build", source_id=str(stale.pk)).exists())
        self.assertFalse(UserNotification.objects.filter(build=stale).exists())

    def test_only_failed_unreferenced_builds_can_be_deleted(self):
        build = self.failed()
        for state in ["queued", "running", "succeeded"]:
            AgentPythonBuild.objects.filter(pk=build.pk).update(status=state)
            response = self.delete(build)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.data["error"]["code"], "PYTHON_BUILD_DELETE_CONFLICT")
        image = AgentRuntimeImage.objects.create(agent=self.agent, tenant=self.tenant, image_ref="protected:v1")
        AgentPythonBuild.objects.filter(pk=build.pk).update(status="failed", image=image)
        self.assertEqual(self.delete(build).status_code, 409)
        self.assertTrue(AgentRuntimeImage.objects.filter(pk=image.pk).exists())

    def test_batch_is_atomic_and_accepts_only_explicit_ids(self):
        one, two = self.failed(), self.failed()
        pending = self.upload().data["id"]
        url = self.url + "delete-failed/"
        response = self.client.post(url, {"build_ids": [str(one.pk), pending]}, format="json", **self.headers)
        self.assertEqual(response.status_code, 409)
        one.refresh_from_db()
        self.assertEqual(one.status, "failed")
        for body in [{}, {"build_ids": []}, {"build_ids": ["invalid"]}, {"build_ids": [str(uuid.uuid4())] * 101}]:
            self.assertEqual(self.client.post(url, body, format="json", **self.headers).status_code, 400)
        response = self.client.post(url, {"build_ids": [str(one.pk), str(two.pk)]}, format="json", **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data["deleted_ids"]), {str(one.pk), str(two.pk)})
        self.assertEqual(AgentPythonBuild.objects.get(pk=pending).status, "queued")

    def test_deletion_frees_revision_capacity_before_cleanup(self):
        build = self.failed()
        AgentPythonBuild.objects.bulk_create([
            AgentPythonBuild(agent=self.agent, filename="old.py", source="old", status="failed") for _ in range(99)
        ])
        self.assertEqual(self.upload().status_code, 400)
        self.assertEqual(self.delete(build).status_code, 200)
        self.assertEqual(self.upload().status_code, 202)

    def test_older_failed_builds_remain_reachable_outside_timeline(self):
        build = self.failed()
        AgentPythonBuild.objects.bulk_create([
            AgentPythonBuild(agent=self.agent, filename="ready.py", status="succeeded") for _ in range(20)
        ])
        data = self.client.get(self.url, **self.headers).data
        self.assertEqual(len(data["results"]), 20)
        self.assertNotIn(str(build.pk), [row["id"] for row in data["results"]])
        self.assertEqual(data["failed_builds"], [{"id": str(build.pk), "filename": "agent.py"}])



class PythonBuildCleanupGuards:
    def test_late_worker_cannot_commit_or_restore_deleted_build(self):
        self.upload()
        build = claim_build("worker")
        def finish_late(*_):
            AgentPythonBuild.objects.filter(pk=build.pk).update(status="failed")
            self.assertEqual(self.delete(build).status_code, 200)
            return {"digest": DIGEST, "image_ref": "late:v1", "tool_count": 1, "dependencies": []}
        with mock.patch("apps.agents.python_builder.build_image", side_effect=finish_late):
            execute_build(build)
        build.refresh_from_db()
        self.assertEqual(build.status, "deleted")
        self.assertFalse(AgentRuntimeImage.objects.exists())

    def test_cleanup_is_retried_and_bound_to_assigned_host(self):
        build = self.failed()
        self.delete(build)
        with mock.patch("apps.agents.python_build_cleanup.docker", side_effect=OSError("private-internal-error")):
            self.assertEqual(cleanup_deleted_builds(), 0)
        build.refresh_from_db()
        self.assertEqual(build.stage, "cleanup_pending")
        self.assertEqual(build.error_message, "")
        with override_settings(NEXUS_AGENT_RUNTIME_HOST_ID="another-host"), mock.patch("apps.agents.python_build_cleanup.docker") as docker:
            self.assertEqual(cleanup_deleted_builds(), 0)
            docker.assert_not_called()

    def test_cleanup_only_removes_own_unreferenced_tag_and_exact_artifact(self):
        build = self.failed()
        self.delete(build)
        with tempfile.TemporaryDirectory() as directory, override_settings(NEXUS_AGENT_STORAGE_ROOT=directory):
            target = Path(directory) / str(self.tenant.pk) / str(self.agent.pk) / "runtime-images" / build.pk.hex / "agent-image.tar"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"orphan")
            sibling = target.parent.parent / "keep.tar"
            sibling.write_bytes(b"keep")
            info = json.dumps([{"Id": DIGEST, "Config": {"Labels": {"nexus.managed": "python-build"}}}]).encode()
            with mock.patch("apps.agents.python_build_cleanup.docker", side_effect=[b"", DIGEST.encode(), info, b""]) as docker:
                self.assertEqual(cleanup_deleted_builds(), 1)
            self.assertEqual(docker.call_args.args[0], ["image", "rm", "--no-prune", f"nexus-python/{self.agent.pk.hex}:{build.pk.hex}"])
            self.assertFalse(target.exists())
            self.assertEqual(sibling.read_bytes(), b"keep")
            self.assertFalse(AgentPythonBuild.objects.filter(pk=build.pk).exists())

    def test_cleanup_preserves_registered_images_and_unowned_container(self):
        build = self.failed()
        self.delete(build)
        AgentRuntimeImage.objects.create(agent=self.agent, tenant=self.tenant, image_ref="existing:v1", image_digest=DIGEST)
        info = json.dumps([{"Id": DIGEST, "Config": {"Labels": {"nexus.managed": "python-build"}}}]).encode()
        with mock.patch("apps.agents.python_build_cleanup.docker", side_effect=[b"", DIGEST.encode(), info]) as docker:
            self.assertEqual(cleanup_deleted_builds(), 0)
            self.assertFalse(any(call.args[0][:2] == ["image", "rm"] for call in docker.call_args_list))
        with mock.patch("apps.agents.python_build_cleanup.docker", side_effect=[b"container", b'[{"Config":{"Labels":{}}}]']) as docker:
            self.assertEqual(cleanup_deleted_builds(), 0)
            self.assertEqual(docker.call_count, 2)

    def test_cleanup_preserves_referenced_artifact_and_outside_symlink(self):
        build = self.failed()
        self.delete(build)
        with tempfile.TemporaryDirectory() as directory, override_settings(NEXUS_AGENT_STORAGE_ROOT=directory):
            relative = f"{self.tenant.pk}/{self.agent.pk}/runtime-images/{build.pk.hex}/agent-image.tar"
            target = Path(directory) / relative
            target.parent.mkdir(parents=True)
            target.write_bytes(b"registered")
            image = AgentRuntimeImage.objects.create(agent=self.agent, tenant=self.tenant, image_ref="protected:v2", artifact_path=relative)
            with mock.patch("apps.agents.python_build_cleanup.docker", return_value=b""):
                self.assertEqual(cleanup_deleted_builds(), 0)
            self.assertEqual(target.read_bytes(), b"registered")
            # A forged symlink resolution is rejected even on Windows hosts that
            # cannot create real symlinks without administrator privileges.
            image.artifact_path = "another-file"
            image.save()
            real_resolve = Path.resolve
            def unsafe_resolve(path, *args, **kwargs):
                return Path(directory).parent / "outside.tar" if path.name == "agent-image.tar" else real_resolve(path, *args, **kwargs)
            with mock.patch.object(Path, "resolve", unsafe_resolve), mock.patch("apps.agents.python_build_cleanup.docker", return_value=b""):
                self.assertEqual(cleanup_deleted_builds(), 0)
            self.assertEqual(target.read_bytes(), b"registered")
