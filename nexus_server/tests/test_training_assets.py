import json
import tempfile

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient
from apps.tenancy.models import Tenant, Membership
from apps.datasets.models import Dataset, DatasetFile, DatasetVersion
from apps.datasets.training_schema import normalize_trajectory, assign_splits, content_digest, messages_from_events
from apps.datasets.training_services import read_sample
from nexus_personal.services import provision_owner
from rest_framework.authtoken.models import Token


def trajectory(task="task-a", status="completed"):
    return {"context": {"task_id": task, "session_id": task, "client": "test-agent"}, "execution_status": status,
        "tools": [{"type": "function", "function": {"name": "add", "description": "Add", "parameters": {
            "type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}, "required": ["a", "b"]}}}],
        "messages": [{"role": "user", "content": "Add 2 and 3"}, {"role": "assistant", "tool_calls": [{"id": "call_1", "type": "function",
            "function": {"name": "add", "arguments": '{"a":2,"b":3}'}}]}, {"role": "tool", "tool_call_id": "call_1", "content": "5"},
            {"role": "assistant", "content": "The answer is 5."}]}


class TrainingSchemaTests(SimpleTestCase):
    def test_agui_snapshot_camel_case_tool_fields_are_reconstructed(self):
        from types import SimpleNamespace
        value = trajectory()
        messages = value["messages"]
        messages[1]["toolCalls"] = messages[1].pop("tool_calls")
        messages[2]["toolCallId"] = messages[2].pop("tool_call_id")
        restored, warnings = messages_from_events([SimpleNamespace(event_type="MESSAGES_SNAPSHOT",
            redacted_payload_json={"type": "MESSAGES_SNAPSHOT", "messages": messages})])
        self.assertFalse(warnings)
        self.assertEqual(normalize_trajectory({**value, "messages": restored})[1], [])

    def test_call_id_changes_do_not_hide_duplicate_content(self):
        one = normalize_trajectory(trajectory())[0]
        two = normalize_trajectory(trajectory("other-task"))[0]
        two["messages"][1]["tool_calls"][0]["id"] = "different-id"
        two["messages"][2]["tool_call_id"] = "different-id"
        self.assertEqual(content_digest(one), content_digest(two))

    def test_complete_tool_trajectory_and_redaction_preserve_ids(self):
        value = trajectory()
        value["messages"][0]["content"] += " Contact person@example.com"
        value["messages"][1]["tool_calls"][0]["id"] = "123456789012"
        value["messages"][2]["tool_call_id"] = "123456789012"
        clean, warnings, groups = normalize_trajectory(value)
        self.assertEqual(warnings, [])
        self.assertNotIn("person@example.com", json.dumps(clean))
        self.assertEqual(clean["messages"][2]["tool_call_id"], "123456789012")
        self.assertEqual(len(groups), 2)

    def test_missing_tool_result_is_retained_but_flagged(self):
        value = trajectory(status="failed")
        del value["messages"][2]
        clean, warnings, _ = normalize_trajectory(value)
        self.assertEqual(clean["execution_status"], "failed")
        self.assertIn("missing_tool_result", warnings)

    def test_structured_secrets_redacted_without_damaging_numeric_arguments(self):
        value = trajectory()
        value["tools"][0]["function"]["parameters"]["properties"]["password"] = {"type": "string"}
        value["messages"][1]["tool_calls"][0]["function"]["arguments"] = '{"a":123456789012,"b":3,"password":"private"}'
        value["messages"][2]["content"] = '{"password":"private","answer":123456789015}'
        clean, warnings, _ = normalize_trajectory(value)
        self.assertEqual(warnings, [])
        self.assertEqual(json.loads(clean["messages"][1]["tool_calls"][0]["function"]["arguments"])["a"], 123456789012)
        self.assertNotIn("private", json.dumps(clean))
        self.assertEqual(clean["tools"][0]["function"]["parameters"]["properties"]["password"], {"type": "string"})
        self.assertEqual(normalize_trajectory(clean)[0], clean)

    def test_invalid_arguments_and_missing_definitions(self):
        value = trajectory()
        value["tools"] = []
        value["messages"][1]["tool_calls"][0]["function"]["arguments"] = "{bad"
        _, warnings, _ = normalize_trajectory(value)
        self.assertIn("invalid_tool_arguments", warnings)
        self.assertIn("missing_tool_definition", warnings)

    def test_non_terminal_and_bad_roles_rejected(self):
        value = trajectory(status="running")
        with self.assertRaises(ValidationError): normalize_trajectory(value)
        value = trajectory(); value["messages"][0]["role"] = "developer-secret"
        with self.assertRaises(ValidationError): normalize_trajectory(value)

    def test_transitive_tasks_sessions_duplicates_do_not_cross_splits(self):
        rows = [{"id": "a", "content_hash": "1", "group_keys": ["task-a", "session-x"]},
                {"id": "b", "content_hash": "2", "group_keys": ["task-b", "session-x"]},
                {"id": "c", "content_hash": "3", "group_keys": ["task-b", "session-y"]},
                {"id": "d", "content_hash": "3", "group_keys": ["task-c"]}]
        for seed in ("v1", "v2", "v3"):
            split = assign_splits(rows, seed, 30, 30)
            self.assertEqual(len(set(split.values())), 1)
            self.assertEqual(split, assign_splits(list(reversed(rows)), seed, 30, 30))


class TrainingAssetsTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.override = override_settings(NEXUS_DATASET_STORAGE_ROOT=self.temp.name, NEXUS_DATASET_STORAGE_BACKEND="local")
        self.override.enable(); self.addCleanup(self.override.disable)
        row = provision_owner(email="training-owner@example.test", password="Strong-test-password-765!")
        self.owner, self.tenant = row.owner, row.tenant
        self.member = get_user_model().objects.create_user(username="training-outsider")
        self.dataset = Dataset.objects.create(tenant=self.tenant, project=row.project,
            name="Agent trajectories", created_by=self.owner)
        self.token = Token.objects.create(user=self.owner)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.token.key)
        self.headers = {}
        self.base = f"/api/v1/datasets/{self.dataset.pk}/training"

    def post(self, suffix, data, status=201):
        response = self.client.post(self.base + suffix, data, format="json", **self.headers)
        self.assertEqual(response.status_code, status, response.content[:1000])
        return response.json() if status < 400 else response

    def add(self, key="attempt-1", value=None):
        return self.post("/samples/", {"request_key": key, "rights_confirmed": True, "trajectory": value or trajectory()})

    def review(self, sample, outcome="success", **fields):
        response = self.client.patch(self.base + f"/samples/{sample['id']}/", {"revision": sample["revision"],
            "decision": "approved", "outcome": outcome, "score": 1, "feedback": "Verified", **fields}, format="json", **self.headers)
        return response

    def test_ingest_idempotency_scope_consent_and_quota(self):
        sample = self.add()
        self.assertEqual(sample["id"], self.add()["id"])
        self.assertEqual(DatasetFile.objects.filter(dataset=self.dataset).count(), 1)
        self.dataset.refresh_from_db(); self.assertGreater(self.dataset.size_bytes, 0)
        self.post("/samples/", {"request_key": "attempt-1", "rights_confirmed": True, "trajectory": trajectory("different")}, 400)
        self.post("/samples/", {"request_key": "no-rights", "rights_confirmed": False, "trajectory": trajectory()}, 400)
        outsider_token = Token.objects.create(user=self.member)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + outsider_token.key)
        response = self.client.get(self.base + "/samples/")
        self.assertEqual(response.status_code, 401)
        self.client.credentials()
        self.assertEqual(self.client.get(self.base + "/samples/").status_code, 401)

    def test_incomplete_failure_is_browsable_not_approvable(self):
        value = trajectory(status="failed"); del value["messages"][2]
        sample = self.add(value=value)
        self.assertIn("missing_tool_result", sample["warnings"])
        self.assertEqual(self.review(sample, outcome="failure").status_code, 400)
        response = self.client.get(self.base + f"/samples/{sample['id']}/", **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["trajectory"]["execution_status"], "failed")

    def test_review_revision_step_selection_and_immutable_export(self):
        sample = self.add()
        approved = self.review(sample, trainable_assistant_indices=[3])
        self.assertEqual(approved.status_code, 200, approved.content)
        self.assertEqual(self.review(sample).status_code, 400)
        version = self.post("/exports/", {"request_key": "release-1", "seed": "fixed", "validation_percent": 0, "test_percent": 0})
        self.assertEqual(version["training"]["counts"], {"train": 1, "validation": 0, "test": 0})
        file = next(f for f in version["files"] if f["file_name"].endswith("train.jsonl"))
        r = self.client.get(file["download_url"], **self.headers)
        self.assertEqual(r.status_code, 200)
        row = json.loads(b"".join(r.streaming_content))
        self.assertEqual(row["messages"][1]["weight"], 0)
        self.assertEqual(row["messages"][3]["weight"], 1)
        self.assertEqual(row["tools"][0]["function"]["name"], "add")
        second = self.review(approved.json(), outcome="failure")
        self.assertEqual(second.status_code, 200)
        r = self.client.get(file["download_url"], **self.headers)
        self.assertEqual(json.loads(b"".join(r.streaming_content)), row)
        replay = self.post("/exports/", {"request_key": "release-1", "seed": "fixed", "validation_percent": 0, "test_percent": 0})
        self.assertEqual(replay["id"], version["id"])
        self.assertEqual(DatasetVersion.objects.count(), 1)

    def test_failures_need_explicit_export_option(self):
        sample = self.add(value=trajectory(status="failed"))
        self.assertEqual(self.review(sample, outcome="failure").status_code, 200)
        self.post("/exports/", {"request_key": "success-only", "validation_percent": 0, "test_percent": 0}, 400)
        version = self.post("/exports/", {"request_key": "with-failures", "include_failures": True, "validation_percent": 0, "test_percent": 0})
        self.assertEqual(version["training"]["counts"]["train"], 1)

    def test_correction_retains_source_group_and_original_bytes(self):
        original = self.add()
        before = read_sample(DatasetFile.objects.get(id=original["id"]))
        changed = trajectory("renamed-task"); changed["messages"][-1]["content"] = "Corrected: 5"
        corrected = self.post("/samples/", {"request_key": "correction-1", "rights_confirmed": True,
            "correction_of": original["id"], "trajectory": changed})
        self.assertEqual(corrected["source"]["kind"], "correction")
        self.assertTrue(set(original["group_keys"]).issubset(corrected["group_keys"]))
        self.assertEqual(read_sample(DatasetFile.objects.get(id=original["id"])), before)

    def test_public_collection_requires_explicit_unpublish(self):
        self.dataset.visibility = "public"; self.dataset.save(update_fields=["visibility"])
        self.post("/samples/", {"request_key": "public", "rights_confirmed": True, "trajectory": trajectory()}, 400)

    def test_deduplication_and_tamper_detection(self):
        for key in ("a", "b"):
            sample = self.add(key)
            self.assertEqual(self.review(sample).status_code, 200)
        version = self.post("/exports/", {"request_key": "dedup", "validation_percent": 0, "test_percent": 0})
        self.assertEqual(version["training"]["counts"]["train"], 1)
        self.assertEqual(version["training"]["skipped_count"], 1)
        file = DatasetFile.objects.get(id=sample["id"])
        file.sha256 = "0" * 64
        with self.assertRaises(ValidationError): read_sample(file)
