"""Run original scale guarantees on actual owner-bound Personal Inbox APIs."""
from django.test import SimpleTestCase, TestCase
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from apps.notifications.models import InboxReceipt
from apps.accounts.models import AccountProfile

from tests.work_inbox_scale_guards import WorkInboxScaleGuards, InboxStreamScaleGuards
from . import test_work_inbox as inbox_tests


class PersonalWorkInboxScaleTests(WorkInboxScaleGuards, TestCase):
    setUp = inbox_tests.PersonalWorkInboxTests.setUp
    get = inbox_tests.PersonalWorkInboxTests.get
    post = inbox_tests.PersonalWorkInboxTests.post
    inbox_payload = staticmethod(inbox_tests.PersonalWorkInboxTests.inbox_payload)
    # Personal maintenance requires a real allowlisted source name. Both tests
    # work on Job records; each receives its own isolated database transaction.
    inbox_daily_batch_name = "jobs"
    inbox_retry_batch_name = "jobs"

    def other_client(self):
        user = get_user_model().objects.create_user(username="scale-other", email="scale-other@example.test")
        AccountProfile.objects.create(user=user, tenant_id=str(self.tenant.pk), project_id=str(self.project.pk))
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer " + Token.objects.create(user=user).key)
        return client

    def test_65_job_rows_keep_cursor_context_and_foreign_identity_out(self):
        items = self.seed(65)
        ids, cursor = [], None
        for _ in range(3):
            params = {"state": "completed", "include_counts": "0"}
            if cursor:
                params["cursor"] = cursor
            response = self.get("items/", params)
            self.assertEqual(response.status_code, 200, response.content)
            data = response.json()
            ids.extend(row["id"] for row in data["results"])
            cursor = data["next_cursor"]
        self.assertEqual(len(ids), 65)
        self.assertEqual(set(ids), {str(item.pk) for item in items})
        self.assertIsNone(cursor)
        self.assertEqual(self.get(f"items/{ids[-1]}/").json()["id"], ids[-1])
        token = self.get("items/", {"state": "completed"}).json()["next_cursor"]
        self.assertEqual(self.get("items/", {"state": "failed", "cursor": token}).status_code, 400)
        other = self.other_client()
        for path, params in ((f"items/{ids[-1]}/", {}), ("items/", {"cursor": token, "state": "completed"})):
            response = other.get("/api/v1/inbox/" + path, params, **self.headers)
            self.assertEqual(response.status_code, 401, response.content)
            self.assertIs(response.json()["ok"], False)
            self.assertEqual(response.json()["error"]["code"], "AUTHENTICATION_FAILED")
            self.assertEqual(response.json()["error"]["message"], "This identity cannot sign in to this personal instance.")
            self.assertNotIn(ids[-1], response.content.decode())
        self.assertFalse(InboxReceipt.objects.exists())

    def test_650_job_receipts_keep_watermark_replay_and_owner_boundary(self):
        items = self.seed(650)
        first = self.post("read-all/").json()
        self.assertEqual(first["marked_read"], 500)
        arrival = self.seed(1)[0]
        second = self.post("read-all/", {"cursor": first["next_cursor"]}).json()
        self.assertEqual(second["marked_read"], 150)
        self.assertIsNone(second["next_cursor"])
        self.assertFalse(InboxReceipt.objects.filter(item=arrival).exists())
        self.assertEqual(self.post("read-all/", {"cursor": first["next_cursor"]}).json()["marked_read"], 0)
        snapshot = set(InboxReceipt.objects.values_list("item_id", "user_id"))
        self.assertEqual(snapshot, {(item.pk, self.user.pk) for item in items})
        response = self.other_client().post("/api/v1/inbox/read-all/",
            {"cursor": first["next_cursor"]}, format="json", **self.headers)
        self.assertEqual(response.status_code, 401, response.content)
        self.assertIs(response.json()["ok"], False)
        self.assertEqual(response.json()["error"]["code"], "AUTHENTICATION_FAILED")
        self.assertEqual(response.json()["error"]["message"], "This identity cannot sign in to this personal instance.")
        self.assertEqual(set(InboxReceipt.objects.values_list("item_id", "user_id")), snapshot)


class PersonalInboxStreamScaleTests(InboxStreamScaleGuards, SimpleTestCase):
    # Original first-event unit test retains explicit revision/auth stubs.
    # It proves prompt iteration without a summary scan, not authenticated SSE.
    pass
