"""Actual Personal Inbox pagination, acknowledgement and identity boundaries."""
import time
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core import signing
from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.notifications.models import InboxItem, InboxReceipt
from . import test_inbox_http as inbox_http
from .test_installation import PASSWORD


class PersonalInboxDeliveryControlTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        inbox_http.PersonalInboxHTTPTests.setUpTestData.__func__(cls)

    setUp = inbox_http.PersonalInboxHTTPTests.setUp
    display_token = inbox_http.PersonalInboxHTTPTests.display_token
    finish = inbox_http.PersonalInboxHTTPTests.finish
    listing = inbox_http.PersonalInboxHTTPTests.listing

    def items(self, count, **changes):
        # Bulk fixture rows exercise the actual 30/500 row HTTP boundaries.
        # Source event generation is separately covered through finish/services.
        batch = uuid4().hex
        defaults = dict(tenant=self.row.tenant, project=self.row.project,
            recipient=self.row.owner, audience_type="personal",
            audience_key="user:" + str(self.row.owner_id), category="agent",
            kind="run_completed", state="completed", priority=45, source_type="run",
            source_id=str(self.run.pk), navigation_key="agent_run",
            occurred_at=self.run.completed_at, resolved_at=self.run.completed_at,
            safe_context={"agent_id": str(self.agent.pk), "agent_name": self.agent.name})
        return InboxItem.objects.bulk_create([
            InboxItem(**{**defaults, "event_key": f"delivery-control:{batch}:{index}", **changes})
            for index in range(count)])

    def read_all(self, client=None, **body):
        return (client or self.client).post("/api/v1/inbox/read-all/", body, format="json")

    def test_real_pagination_has_no_duplicates_and_binds_filter_and_owner_context(self):
        original = self.finish()
        rows = self.items(60)
        first = self.listing()
        self.assertEqual(len(first["results"]), 30)
        self.assertEqual(first["counts"]["total"], 61)
        self.assertEqual(first["counts"]["unread"], 61)
        second = self.listing("?cursor=" + first["next_cursor"])
        third = self.listing("?cursor=" + second["next_cursor"])
        self.assertEqual([len(second["results"]), len(third["results"])], [30, 1])
        ids = [item["id"] for page in (first, second, third) for item in page["results"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), {str(row.pk) for row in [original, *rows]})
        self.assertIsNone(third["next_cursor"])
        for params in ({"cursor": first["next_cursor"], "state": "completed"},
                       {"cursor": first["next_cursor"], "q": "different"},
                       {"cursor": first["next_cursor"] + "tampered"}):
            response = self.client.get("/api/v1/inbox/items/", params)
            self.assertEqual(response.status_code, 400, response.data)
        response = self.client.get("/api/v1/inbox/items/", {"cursor": first["next_cursor"]},
                                   HTTP_X_NEXUS_PROJECT=str(uuid4()))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(InboxReceipt.objects.exists())

    def test_bounded_read_all_excludes_later_arrivals_and_foreign_recipients(self):
        original = self.finish()
        rows = self.items(500)
        other = get_user_model().objects.create_user(username="other-receipt-owner")
        foreign = self.items(1, recipient=other, audience_key="user:" + str(other.pk))[0]
        first = self.read_all()
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data["marked_read"], 500)
        self.assertTrue(first.data["next_cursor"])
        late = self.items(1)[0]
        second = self.read_all(cursor=first.data["next_cursor"])
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(second.data, {"marked_read": 1, "next_cursor": None})
        replay = self.read_all(cursor=first.data["next_cursor"])
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.data["marked_read"], 0)
        receipts = InboxReceipt.objects.filter(user=self.row.owner, read_at__isnull=False)
        self.assertEqual(set(receipts.values_list("item_id", flat=True)), {row.pk for row in [original, *rows]})
        self.assertFalse(InboxReceipt.objects.filter(item__in=[late, foreign]).exists())
        self.assertFalse(InboxReceipt.objects.exclude(user=self.row.owner).exists())
        self.assertEqual(self.client.get("/api/v1/inbox/summary/").data["unread"], 1)

    def test_read_snapshot_rejects_tampering_expiry_and_other_identity_without_writes(self):
        self.finish()
        self.items(500)
        first = self.read_all()
        self.assertEqual(first.status_code, 200, first.data)
        token = first.data["next_cursor"]
        data = signing.loads(token, salt="inbox-read-all")
        class OldSigner(signing.TimestampSigner):
            def timestamp(self):
                return signing.b62_encode(int(time.time()) - 7200)
        expired = OldSigner(salt="inbox-read-all").sign_object(data)
        other_user = signing.dumps({**data, "user": str(uuid4())}, salt="inbox-read-all")
        other_tenant = signing.dumps({**data, "tenant": str(uuid4())}, salt="inbox-read-all")
        before = list(InboxReceipt.objects.order_by("pk").values_list("pk", "read_at", "updated_at"))
        for invalid in (token + "tampered", expired, other_user, other_tenant):
            response = self.read_all(cursor=invalid)
            self.assertEqual(response.status_code, 400, response.data)
            self.assertEqual(list(InboxReceipt.objects.order_by("pk").values_list("pk", "read_at", "updated_at")), before)

    def test_sessions_require_csrf_for_receipt_mutations(self):
        item = self.finish()
        session = APIClient(enforce_csrf_checks=True)
        self.assertTrue(session.login(username=self.row.owner.username,
                                      password=PASSWORD))
        self.assertEqual(session.get("/api/v1/inbox/items/").status_code, 200)
        paths = ["/api/v1/inbox/read-all/",
                 *(f"/api/v1/inbox/items/{item.pk}/{action}/" for action in ("read", "open", "archive"))]
        for path in paths:
            response = session.post(path, {}, format="json")
            self.assertEqual(response.status_code, 403)
        self.assertFalse(InboxReceipt.objects.exists())
        # Bearer authentication is a distinct non-cookie path and stays usable.
        self.assertEqual(self.client.post(paths[1], {}, format="json").status_code, 200)

    def test_current_identity_is_rechecked_for_stolen_cursor_and_disabled_owner(self):
        self.finish()
        self.items(500)
        first = self.read_all()
        token = first.data["next_cursor"]
        self.assertTrue(token)
        other = get_user_model().objects.create_user(username="inbox-token-stranger")
        credential = Token.objects.create(user=other)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer " + credential.key)
        self.assertIn(self.read_all(client=client, cursor=token).status_code, (401, 403))
        self.assertEqual(InboxReceipt.objects.filter(user=other).count(), 0)
        get_user_model().objects.filter(pk=self.row.owner_id).update(is_active=False)
        response = self.read_all(cursor=token)
        # Disabling the sole installed owner makes the whole Personal context
        # unavailable before authentication, matching all existing endpoints.
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "PERSONAL_SETUP_REQUIRED")
        self.assertEqual(InboxReceipt.objects.count(), 500)
        get_user_model().objects.filter(pk=self.row.owner_id).update(is_active=True)
        recovered = self.read_all(cursor=token)
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(recovered.data, {"marked_read": 1, "next_cursor": None})

    def test_legacy_notification_endpoints_are_not_mounted_for_owner(self):
        item = self.finish()
        for suffix in ("", "summary/", "read-all/", f"{item.pk}/read/", f"{item.pk}/open/"):
            path = "/api/v1/notifications/" + suffix
            response = self.client.get(path) if suffix in ("", "summary/") else self.client.post(path, {}, format="json")
            self.assertEqual(response.status_code, 404)
        self.assertFalse(InboxReceipt.objects.exists())
        self.assertEqual(len(self.listing()["results"]), 1)
