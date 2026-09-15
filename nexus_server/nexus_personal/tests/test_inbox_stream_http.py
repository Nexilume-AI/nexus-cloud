"""Exercise SSE negotiation through authenticated HTTP routing."""
from asgiref.sync import async_to_sync
from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


class InboxStreamHTTPTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        owner = provision_owner(email="stream-owner@example.test", password=PASSWORD)
        cls.token = Token.objects.create(user=owner.owner)

    def test_browser_accept_header_receives_ready_without_waiting_for_heartbeat(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer " + self.token.key)
        response = client.get("/api/v1/inbox/stream/", HTTP_ACCEPT="text/event-stream")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.streaming)
        self.assertIn("text/event-stream", response["Content-Type"])
        self.assertEqual(response["X-Accel-Buffering"], "no")

        async def first_event():
            iterator = response.streaming_content
            try:
                return await anext(iterator)
            finally:
                await iterator.aclose()

        self.assertIn(b"event: ready\n", async_to_sync(first_event)())
        # The test client closes the response when its async iterator is closed.
        # A second close would send request_finished inside TestCase's transaction.
        self.assertTrue(response.closed)

    def test_stream_does_not_allow_anonymous_subscribers(self):
        response = APIClient().get("/api/v1/inbox/stream/", HTTP_ACCEPT="text/event-stream")
        self.assertIn(response.status_code, (401, 403))
        self.assertFalse(response.streaming)
