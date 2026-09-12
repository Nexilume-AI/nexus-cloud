"""Shared Mobile protocol over real Personal owner auth; no physical phone claim."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from apps.accounts.models import AccountProfile
from apps.mobile.models import MobileDevice, MobileCommand
from nexus_personal.services import provision_owner
from tests.mobile_http_guards import MobileHTTPGuards
from .test_installation import PASSWORD


class PersonalMobileProtocolTests(MobileHTTPGuards, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email="mobile-protocol@example.test", password=PASSWORD)

    def setUp(self):
        self.owner, self.tenant = self.row.owner, self.row.tenant
        self.assertFalse(self.owner.is_superuser)
        self.client = APIClient(enforce_csrf_checks=True)
        self.assertTrue(self.client.login(username=self.owner.username, password=PASSWORD))
        self.client.get("/api/v1/public/bootstrap/")
        self.client.credentials(HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value)

    def mobile_payload(self, response):
        return response.json()

    def assert_other_mobile_caller_denied(self, device, png):
        other = get_user_model().objects.create_user(username="other-mobile-viewer")
        AccountProfile.objects.create(user=other, tenant_id=str(self.tenant.pk),
            project_id=str(self.row.project_id), status="active")
        token = Token.objects.create(user=other)
        outsider = APIClient()
        outsider.credentials(HTTP_AUTHORIZATION="Bearer " + token.key)
        denied = outsider.get(f"/api/v1/mobile-devices/{device.pk}/screenshot/", HTTP_ACCEPT="*/*")
        # A valid non-owner token is not authorized to sign into this instance.
        self.assertEqual(denied.status_code, 401)
        self.assertNotIn(png, denied.content)

    def test_owner_csrf_and_context_are_required_to_create_devices(self):
        self.client.credentials()
        data = {"name": "Phone", "platform": "android"}
        self.assertEqual(self.client.post("/api/v1/mobile-devices/", data, format="json").status_code, 403)
        self.assertEqual(APIClient().post("/api/v1/mobile-devices/", data, format="json").status_code, 401)
        self.client.credentials(HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value)
        for header in ("HTTP_X_NEXUS_TENANT", "HTTP_X_NEXUS_PROJECT"):
            response = self.client.post("/api/v1/mobile-devices/", data, format="json", **{header: "foreign"})
            self.assertEqual(response.status_code, 403)
        self.assertFalse(MobileDevice.objects.exists())

    def test_device_transport_token_never_authorizes_caller_command_or_revoke(self):
        device, token = self._create_device()
        peer = APIClient()
        headers = {"HTTP_X_NEXUS_MOBILE_TOKEN": token}
        path = f"/api/v1/mobile-devices/{device.pk}/"
        response = peer.post(path + "commands/", {"action": "observe"}, format="json", **headers)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(peer.delete(path, **headers).status_code, 401)
        self.assertFalse(MobileCommand.objects.exists())
        device.refresh_from_db()
        self.assertNotEqual(device.status, "deleted")
