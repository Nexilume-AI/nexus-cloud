"""Personal image inputs traverse real HTTP with private implementations denied."""
import base64
import hashlib
import json
from io import BytesIO

from PIL import Image
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.audit.models import AuditLog
from apps.gateway.models import GatewayRequestLog
from apps.routers import services as routers
from nexus_personal.models import PersonalGatewayRequest
from .provider_http_fixture import ProviderHTTPFixture
from . import test_router_runtime as runtime_tests


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1")
class PersonalGatewayVisionTests(ProviderHTTPFixture, TestCase):
    source = runtime_tests.PersonalRouterRuntimeTests.source
    post = runtime_tests.PersonalRouterRuntimeTests.post
    runtime = runtime_tests.PersonalRouterRuntimeTests.runtime

    def setUp(self):
        runtime_tests.PersonalRouterRuntimeTests.setUp(self)
        output = BytesIO()
        Image.new("RGB", (4, 4), "blue").save(output, "PNG")
        self.encoded = base64.b64encode(output.getvalue()).decode()
        self.image_url = "data:image/png;base64," + self.encoded
        self.prompt = "Private vision sentinel: 请识别这张图片"
        self.contract(image=True)
        type(self).message_content_hashes.clear()

    def contract(self, *, image):
        contract = {"operations": ["chat.completions", "responses"],
                    "input_modalities": ["text", "image"] if image else ["text"],
                    "output_modalities": ["text"]}
        model = self.source_row.canonical_model
        for key, value in contract.items():
            setattr(model, key, value)
        model.save()
        offer = self.source_row.runtime_model_offer
        offer.metadata = {"model_contract": contract}
        offer.save()

    def send_image(self, family, key, *, url=None, router=None, model=None, client=None, stream=False):
        common = {"model": model or self.model, "router_id": str(router or self.router.pk), "stream": stream}
        image_url = self.image_url if url is None else url
        if family == "responses":
            path = "/api/v1/openai/v1/responses"
            payload = {**common, "input": [{"role": "user", "content": [
                {"type": "input_text", "text": self.prompt},
                {"type": "input_image", "image_url": image_url}]}]}
        elif family == "claude":
            path = "/api/v1/claude/v1/messages"
            payload = {**common, "max_tokens": 64, "messages": [{"role": "user", "content": [
                {"type": "text", "text": self.prompt},
                {"type": "image", "source": {"type": "url", "url": image_url}}]}]}
        else:
            path = "/api/v1/openai/v1/chat/completions"
            payload = {**common, "messages": [{"role": "user", "content": [
                {"type": "text", "text": self.prompt},
                {"type": "image_url", "image_url": {"url": image_url}}]}]}
        return (client or self.client).post(path, payload, format="json",
            HTTP_X_REQUEST_ID=key, **self.headers)

    def test_three_protocols_preserve_image_content_without_persisting_payload(self):
        expected = [{"type": "text", "text": self.prompt},
                    {"type": "image_url", "image_url": {"url": self.image_url}}]
        digest = hashlib.sha256(json.dumps(expected, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        for family in ("chat", "responses", "claude"):
            response = self.send_image(family, family)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertIn("healthy", str(response.data))
            receipt = PersonalGatewayRequest.objects.get(request_id=family)
            self.assertEqual(receipt.state, "completed")
            self.assertEqual(receipt.result_log.deployment_id, self.source_row.pk)
            self.assertEqual(receipt.attempts.count(), 1)
        self.assertEqual(self.message_content_hashes, [digest] * 3)
        self.assertEqual(self.calls, [("POST", "/v1/chat/completions", "personal-model")] * 3)
        persisted = json.dumps([list(model.objects.values()) for model in (
            PersonalGatewayRequest, GatewayRequestLog, AuditLog)], default=str, ensure_ascii=False)
        for secret in (self.prompt, self.encoded, "local-provider-test-key"):
            self.assertNotIn(secret, persisted)

    def test_aggregation_forwards_image_to_execution_and_refuses_duplicate_request(self):
        aggregate = routers.create_router(request=self.request(), name="vision-aggregate", router_type="aggregation")
        routers.add_router_child_binding(request=self.request(), router_id=str(aggregate.pk), data={
            "child_output_id": self.router.outputs.get().pk, "exposed_model_name": "vision-model"})
        routers.deploy_router(request=self.request(), router_id=str(aggregate.pk))
        for family in ("chat", "responses", "claude"):
            reply = self.send_image(family, "aggregate-" + family, router=aggregate.pk, model="vision-model")
            self.assertEqual(reply.status_code, 200, reply.data)
            replay = self.send_image(family, "aggregate-" + family, router=aggregate.pk, model="vision-model")
            self.assertEqual(replay.status_code, 409, replay.data)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(len(self.message_content_hashes), 3)

    def test_text_only_offer_rejects_image_before_dispatch_on_every_protocol(self):
        self.contract(image=False)
        for family in ("chat", "responses", "claude"):
            response = self.send_image(family, "text-only-" + family)
            self.assertEqual(response.status_code, 400, response.data)
            self.assertIn("MODEL_OPERATION_UNSUPPORTED", str(response.data))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.message_content_hashes, [])
        self.assertFalse(PersonalGatewayRequest.objects.exists())

    def test_foreign_owner_and_unsafe_urls_cannot_reach_upstream(self):
        foreign = APIClient()
        foreign.force_login(self.other)
        for family in ("chat", "responses", "claude"):
            self.assertIn(self.send_image(family, "foreign-" + family, client=foreign).status_code, (401, 403))
            for index, url in enumerate(("http://127.0.0.1/secret", "http://[::1]/secret", "file:///secret",
                                          "data:text/plain;base64,c2VjcmV0")):
                response = self.send_image(family, f"unsafe-{family}-{index}", url=url)
                self.assertEqual(response.status_code, 400, response.data)
                self.assertNotIn(url, str(response.data))
        self.assertEqual(self.calls, [])
        self.assertFalse(PersonalGatewayRequest.objects.exists())

    @override_settings(NEXUS_MULTIMODAL_DATA_URL_MAX_BYTES=16)
    def test_oversized_image_is_rejected_without_receipt_or_upstream_request(self):
        for family in ("chat", "responses", "claude"):
            response = self.send_image(family, "oversize-" + family)
            self.assertEqual(response.status_code, 400, response.data)
            self.assertNotIn(self.encoded, str(response.data))
        self.assertEqual(self.calls, [])
        self.assertFalse(PersonalGatewayRequest.objects.exists())
