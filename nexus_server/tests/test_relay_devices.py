import secrets
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate
from apps.accounts import relay_devices as v
from apps.accounts.models import RelayDevice


@override_settings(NEXUS_PUBLIC_BASE_URL="https://cloud.example")
class RelayDeviceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="relay-owner", password="test-password-only")
        self.other = get_user_model().objects.create_user(username="other-owner")
        self.f = APIRequestFactory(enforce_csrf_checks=True)
        self.spec = {"name": "Clef", "model": "clef", "endpoint": "https://model.example/v1", "protocol": "system_one"}
        self.scope = patch.object(v, "scope_request", return_value=object())
        self.scope_mock = self.scope.start()
        self.addCleanup(self.scope.stop)

    def call(self, view, data=None, token=None, user=None, method="post", **kwargs):
        req = getattr(self.f, method)("/api/v1/auth/relay/test/", data or {}, format="json",
            **({"HTTP_AUTHORIZATION": "Bearer " + token} if token else {}))
        if user:
            force_authenticate(req, user=user)
        return view.as_view()(req, **kwargs)

    def start(self):
        r = self.call(v.RelayStart, {"name": "Test host", "models": [self.spec]})
        self.assertEqual(r.status_code, 201)
        return r.data

    def connect(self):
        d = self.start()
        r = self.call(v.RelayApproval, {"code": d["user_code"], "approve": True,
            "tenant_id": "b2bf6107-bd13-4f9c-baf9-773f90aeb67e"}, user=self.user)
        self.assertEqual(r.status_code, 200)
        t = self.call(v.RelayToken, {"device_code": d["device_code"]})
        self.assertEqual(t.status_code, 200)
        return d, t.data

    def test_explicit_approval_one_use_rotation_and_revoke(self):
        d, token = self.connect()
        self.assertEqual(self.call(v.RelayToken, {"device_code": d["device_code"]}).status_code, 400)
        row = RelayDevice.objects.get(id=d["device_id"])
        self.assertNotIn(token["access_token"], str(row.__dict__))
        self.assertNotIn(token["refresh_token"], str(row.__dict__))
        renewed = self.call(v.RelayToken, {"refresh_token": token["refresh_token"]})
        self.assertEqual(renewed.status_code, 200)
        self.assertNotEqual(renewed.data["access_token"], token["access_token"])
        self.assertGreaterEqual(self.call(v.RelayModels, token=token["access_token"], method="get").status_code, 400)
        self.assertGreaterEqual(self.call(v.RelayToken, {"refresh_token": token["refresh_token"]}).status_code, 400)
        with patch("apps.accounts.relay_registration.disable_sources") as disable:
            r = self.call(v.RelayRevoke, {}, renewed.data["access_token"])
            self.assertEqual(r.status_code, 200)
            disable.assert_called_once()
        self.assertGreaterEqual(self.call(v.RelayToken, {"refresh_token": renewed.data["refresh_token"]}).status_code, 400)

    def test_poll_backoff_and_expiry(self):
        d = self.start()
        r = self.call(v.RelayToken, {"device_code": d["device_code"]})
        self.assertEqual(r.data["error"], "authorization_pending")
        self.assertEqual(self.call(v.RelayToken, {"device_code": d["device_code"]}).status_code, 429)
        RelayDevice.objects.filter(id=d["device_id"]).update(approval_expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.call(v.RelayToken, {"device_code": d["device_code"]}).data["error"], "expired_token")

    def test_denied_and_unauthenticated_approval(self):
        d = self.start()
        data = {"code": d["user_code"], "approve": False}
        self.assertGreaterEqual(self.call(v.RelayApproval, data).status_code, 400)
        self.assertEqual(self.call(v.RelayApproval, data, user=self.user).status_code, 200)
        self.assertEqual(self.call(v.RelayToken, {"device_code": d["device_code"]}).data["error"], "access_denied")

    def test_scope_rechecked_and_unapproved_model_never_registered(self):
        d, token = self.connect()
        with patch("apps.accounts.relay_registration.register") as register:
            r = self.call(v.RelayModels, {"model": "foreign", "endpoint_api_key": "private"}, token["access_token"])
            self.assertEqual(r.status_code, 403)
            register.assert_not_called()
        from rest_framework.exceptions import PermissionDenied
        self.scope_mock.side_effect = PermissionDenied()
        self.assertEqual(self.call(v.RelayToken, {"refresh_token": token["refresh_token"]}).status_code, 403)

    def test_other_user_cannot_revoke(self):
        d, _ = self.connect()
        self.assertEqual(self.call(v.RelayBrowserRevoke, {}, user=self.other, device_id=d["device_id"]).status_code, 404)

    def test_invalid_url_duplicates_and_missing_confirmation(self):
        for endpoint in ["http://example.com", "https://user:pass@example.com", "https://example.com/?key=secret"]:
            r = self.call(v.RelayStart, {"name": "Bad", "models": [{**self.spec, "endpoint": endpoint}]})
            self.assertEqual(r.status_code, 400)
        self.assertEqual(self.call(v.RelayStart, {"name": "Bad", "models": [self.spec, self.spec]}).status_code, 400)
        d = self.start()
        self.assertEqual(self.call(v.RelayApproval, {"code": d["user_code"]}, user=self.user).status_code, 400)

    def test_expired_access_requires_refresh(self):
        d, token = self.connect()
        RelayDevice.objects.filter(id=d["device_id"]).update(access_expires_at=timezone.now()-timedelta(seconds=1))
        self.assertGreaterEqual(self.call(v.RelayModels, token=token["access_token"], method="get").status_code, 400)
        self.assertEqual(self.call(v.RelayToken, {"refresh_token": token["refresh_token"]}).status_code, 200)

    def test_page_contains_no_inline_unsafe_interpolation(self):
        from apps.accounts.relay_page import page
        r = page(self.f.get("/?code=<script>"))
        self.assertNotIn(b"<script>", r.content)
        self.assertIn("frame-ancestors 'none'", r["Content-Security-Policy"])
        self.assertIn(b"m.name+'\\n'", r.content)
