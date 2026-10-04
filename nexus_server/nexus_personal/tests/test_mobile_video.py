"""Community video HTTP contract using the real installation owner and device token."""
from datetime import timedelta
from uuid import uuid4

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.mobile.models import MobileVideoSession
from .test_mobile_http import PersonalMobileHTTPTests


@override_settings(NEXUS_MOBILE_TURN_URLS="")
class PersonalMobileVideoTests(TestCase):
    # Reuse real pairing and authentication, not the Enterprise membership fixture.
    setUpTestData = classmethod(PersonalMobileHTTPTests.setUpTestData.__func__)
    setUp = PersonalMobileHTTPTests.setUp
    create_device = PersonalMobileHTTPTests.create_device
    device_path = PersonalMobileHTTPTests.device_path

    def start(self):
        self.device, self.token = self.create_device()
        self.device.capabilities = {**self.device.capabilities, "live_video": 1, "screen_control": 1}
        self.device.save(update_fields=["capabilities"])
        response = self.client.post(self.device_path(self.device, "video/"), {}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response.data["transport"], "direct")
        return response.data["id"]

    def signal(self, session, kind, *, device=False, **values):
        client = self.device_client if device else self.client
        return client.post(f"/api/v1/mobile-video/{session}/" + ("device/" if device else ""),
            {"type": kind, "message_id": str(uuid4()), **values}, format="json")

    def test_real_owner_and_device_negotiate_and_stop_video(self):
        session = self.start()
        self.assertEqual(self.signal(session, "offer", sdp="v=0\r\nfixture").status_code, 200)
        polled = self.device_client.post(self.device_path(self.device, "device/video/"), {}, format="json")
        self.assertEqual(polled.data["session"]["signals"][0]["type"], "offer")
        self.assertEqual(self.signal(session, "answer", device=True, sdp="v=0\r\nfixture").status_code, 200)
        response = self.client.get(f"/api/v1/mobile-video/{session}/?after=1")
        self.assertEqual([item["type"] for item in response.data["signals"]], ["answer"])
        self.assertEqual(self.signal(session, "stop").data["state"], "stopped")
        self.assertEqual(MobileVideoSession.objects.get(pk=session).signals, [])

    def test_device_token_cannot_authorize_viewer_requests(self):
        session = self.start()
        self.assertIn(self.device_client.get(f"/api/v1/mobile-video/{session}/").status_code, (401, 403))
        self.assertIn(self.device_client.post(self.device_path(self.device, "video/"), {}, format="json").status_code, (401, 403))

    def test_foreign_user_cannot_discover_video(self):
        session = self.start()
        from django.contrib.auth import get_user_model
        from apps.accounts.models import AccountProfile
        other = get_user_model().objects.create_user(username="foreign-video-owner")
        AccountProfile.objects.create(user=other)
        foreign = APIClient()
        foreign.credentials(HTTP_AUTHORIZATION="Bearer " + Token.objects.create(user=other).key)
        self.assertIn(foreign.get(f"/api/v1/mobile-video/{session}/").status_code, (401, 403))

    def test_expired_video_rejects_late_signals_and_purges_sdp(self):
        session = self.start()
        self.assertEqual(self.signal(session, "offer", sdp="v=0\r\nfixture").status_code, 200)
        MobileVideoSession.objects.filter(pk=session).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.client.get(f"/api/v1/mobile-video/{session}/").data["state"], "expired")
        self.assertEqual(MobileVideoSession.objects.get(pk=session).signals, [])
        self.assertEqual(self.signal(session, "ice", candidate={}).status_code, 409)

    def test_old_apk_without_video_capability_stays_unavailable(self):
        device, _ = self.create_device()
        response = self.client.post(self.device_path(device, "video/"), {}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(MobileVideoSession.objects.count(), 0)
