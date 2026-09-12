"""Shared Direct API runtime against an actual loopback HTTP upstream, no Cloud."""
import json
import base64
import hashlib
from email.parser import BytesParser
from email.policy import default as email_policy
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from apps.providers.models import ProviderRuntimeAccount
from apps.providers.services import create_provider_account
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


class CheckedHTTPServer(ThreadingHTTPServer):
    """Attribute background failures to the test before its state is reset."""

    def __init__(self, *args, **kwargs):
        self._condition = threading.Condition()
        self._active_requests = 0
        self._handler_errors = 0
        super().__init__(*args, **kwargs)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(5)
        return request, address

    def process_request(self, request, client_address):
        with self._condition:
            self._active_requests += 1
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_finished()
            raise

    def _request_finished(self):
        with self._condition:
            self._active_requests -= 1
            self._condition.notify_all()

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_finished()

    def handle_error(self, request, client_address):
        # Never print request data or exception text. Surface a test failure instead.
        with self._condition:
            self._handler_errors += 1

    def assert_healthy(self, timeout=5):
        with self._condition:
            if not self._condition.wait_for(lambda: self._active_requests == 0, timeout):
                raise AssertionError("Upstream fixture did not become idle")
            errors = self._handler_errors
            self._handler_errors = 0
        if errors:
            raise AssertionError(f"Unexpected upstream handler errors: {errors}")


class ProviderHTTPFixture:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.calls = []
        cls.catalog_invalid = False
        cls.catalog_models = None
        cls.catalog_status = 200
        cls.probe_status = 200
        cls.gateway_failures_remaining = 0
        cls.gateway_stream_mode = "complete"
        cls.image_mode = "complete"
        cls.image_upload_hashes = []
        cls.message_content_hashes = []
        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send_sse(self, body):
                try:
                    self.wfile.write(body)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    self.close_connection = True
                    return False
                return True

            def reply(self, payload):
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                # Do not retain headers or credentials in test evidence.
                cls.calls.append(("GET", self.path))
                if self.headers.get("Authorization") != "Bearer local-provider-test-key":
                    self.send_error(401)
                    return
                if cls.catalog_status != 200:
                    self.send_error(cls.catalog_status)
                    return
                models = cls.catalog_models if cls.catalog_models is not None else ["personal-model"]
                self.reply({"data": [] if cls.catalog_invalid else [{"id": model} for model in models]})

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if self.headers.get("Content-Type", "").startswith("multipart/form-data"):
                    message = BytesParser(policy=email_policy).parsebytes(
                        ("Content-Type: " + self.headers["Content-Type"] + "\r\nMIME-Version: 1.0\r\n\r\n").encode() + body)
                    payload = {}
                    for part in message.iter_parts():
                        name = part.get_param("name", header="content-disposition")
                        data = part.get_payload(decode=True)
                        if part.get_filename():
                            cls.image_upload_hashes.append((name, hashlib.sha256(data).hexdigest()))
                        else:
                            payload[name] = data.decode()
                else:
                    payload = json.loads(body)
                cls.calls.append(("POST", self.path, payload.get("model")))
                if self.headers.get("Authorization") != "Bearer local-provider-test-key":
                    self.send_error(401)
                    return
                for message in payload.get("messages", []):
                    content = json.dumps(message.get("content"), sort_keys=True, ensure_ascii=False).encode()
                    cls.message_content_hashes.append(hashlib.sha256(content).hexdigest())
                if cls.probe_status != 200:
                    self.send_error(cls.probe_status)
                    return
                if "/images/" in self.path:
                    if cls.image_mode == "empty":
                        data = []
                    elif cls.image_mode == "malformed":
                        data = [{"b64_json": "not-a-valid-image"}]
                    elif cls.image_mode == "private_url":
                        data = [{"url": "https://127.0.0.1/private-image.png"}]
                    else:
                        data = [{"b64_json": base64.b64encode(cls.generated_image).decode()}]
                    self.reply({"data": data})
                    return
                if cls.gateway_failures_remaining:
                    cls.gateway_failures_remaining -= 1
                    self.send_error(503)
                    return
                if payload.get("stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    chunks = [{"choices": [{"delta": {"content": "hello "}, "finish_reason": None}]}]
                    if cls.gateway_stream_mode == "error":
                        chunks = [{"error": {"message": "do-not-leak-stream-error"}}]
                    elif cls.gateway_stream_mode == "empty":
                        chunks = []
                    if cls.gateway_stream_mode == "complete":
                        chunks += [{"choices": [{"delta": {"content": "world"}, "finish_reason": "stop"}],
                                    "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}}]
                    for chunk in chunks:
                        if not self.send_sse(("data: " + json.dumps(chunk) + "\n\n").encode()):
                            return
                    if cls.gateway_stream_mode in {"complete", "error", "empty"}:
                        self.send_sse(b"data: [DONE]\n\n")
                    return
                self.reply({"choices": [{"message": {"role": "assistant", "content": "healthy"}}],
                            "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}})
        if hasattr(cls, "configure_upstream"):
            Upstream = cls.configure_upstream(Upstream)
        cls.upstream = CheckedHTTPServer(("127.0.0.1", 0), Upstream)
        cls.thread = threading.Thread(target=cls.upstream.serve_forever, daemon=True)
        cls.thread.start()


    @classmethod
    def tearDownClass(cls):
        try:
            cls.upstream.shutdown()
            cls.upstream.assert_healthy()
        finally:
            cls.upstream.server_close()
            cls.thread.join(timeout=5)
            super().tearDownClass()


    @classmethod
    def setUpTestData(cls):
        cls.installation = provision_owner(email="runtime-owner@example.test", password=PASSWORD)
        cls.other = get_user_model().objects.create_user(username="foreign-provider-owner")


    def setUp(self):
        self.upstream.assert_healthy()
        self.addCleanup(self.upstream.assert_healthy)
        type(self).calls.clear()
        type(self).catalog_invalid = False
        type(self).catalog_models = None
        type(self).catalog_status = 200
        type(self).probe_status = 200
        type(self).gateway_failures_remaining = 0
        type(self).gateway_stream_mode = "complete"
        type(self).image_mode = "complete"
        type(self).image_upload_hashes.clear()
        type(self).message_content_hashes.clear()


    def request(self, user=None):
        return SimpleNamespace(user=user or self.installation.owner, META={}, headers={}, query_params={})


    def create(self, name="upstream"):
        account = create_provider_account(request=self.request(), data={
            "provider": "personal-upstream", "name": name, "account_id": name,
            "url": f"http://127.0.0.1:{self.upstream.server_port}/v1", "key": "local-provider-test-key"})
        runtime = ProviderRuntimeAccount.objects.get(source_provider_account=account)
        return account, runtime
