"""A real HTTP fixture checks cache wire semantics through four Router levels."""
import io
import json
from asgiref.sync import async_to_sync
from apps.gateway.models import GatewayRequestLog
from apps.gateway.test_prompt_cache import DETAILS
from .test_router_cascade import RouterCascadeTests


class PromptCacheCascadeTests(RouterCascadeTests):
    @classmethod
    def configure_upstream(cls, base):
        cls.cache_bodies = []
        class Upstream(base):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                payload = json.loads(body)
                if "prompt_cache_key" not in payload:
                    self.rfile = io.BytesIO(body)
                    return super().do_POST()
                if self.headers.get("Authorization") != "Bearer local-provider-test-key":
                    return self.send_error(401)
                cls.cache_bodies.append(payload)
                native = self.path == "/v1/responses"
                usage = ({"input_tokens": 12000, "output_tokens": 20, "input_tokens_details": DETAILS} if native
                         else {"prompt_tokens": 12000, "completion_tokens": 20, "prompt_tokens_details": DETAILS})
                usage["total_tokens"] = 12020
                response = ({"id": "resp_cache", "object": "response", "status": "completed", "output": [], "usage": usage}
                            if native else {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}], "usage": usage})
                if not payload.get("stream"):
                    return self.reply(response)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                chunks = ([{"type": "response.completed", "response": response}] if native else
                          [{"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}], "usage": usage}])
                for chunk in chunks:
                    self.send_sse(("data: " + json.dumps(chunk) + "\n\n").encode())
                if not native:
                    self.send_sse(b"data: [DONE]\n\n")
        return Upstream

    def test_cache_options_breakpoints_and_usage_cross_json_sse_cascade(self):
        parent = self.parent()
        root = self.parent(parent, "nested", "outer")
        client = self.client_for(root)
        for native in (False, True):
            for stream in (False, True):
                with self.subTest(native=native, stream=stream):
                    options = {"mode": "explicit", "ttl": "12h", "prewarm": True}
                    part = {"type": "input_text" if native else "text", "text": "prefix " * 8000,
                            "prompt_cache_breakpoint": {"mode": "explicit", "ttl": "12h"}}
                    content = [{"type": "message", "role": "user", "content": [part]}] if native else [{"role": "user", "content": [part]}]
                    payload = {"model": "outer", "stream": stream, "prompt_cache_key": "fixture-key",
                               "prompt_cache_options": options, "input" if native else "messages": content}
                    before = GatewayRequestLog.objects.count()
                    response = client.post("/api/v1/openai/v1/" + ("responses" if native else "chat/completions"), payload, format="json")
                    self.assertEqual(response.status_code, 200, getattr(response, "data", None))
                    if stream and response.is_async:
                        async def consume():
                            return b"".join([chunk async for chunk in response.streaming_content])
                        raw = async_to_sync(consume)()
                    else:
                        raw = b"".join(response.streaming_content) if stream else json.dumps(response.data).encode()
                    self.assertIn(b'"cache_write_tokens": 7000', raw.replace(b'":7000', b'": 7000'))
                    self.assertEqual(GatewayRequestLog.objects.count() - before, 1)
                    log = GatewayRequestLog.objects.latest("created_at")
                    self.assertEqual(len(log.routing_trace["path"]), 4)
                    self.assertEqual(log.cache_usage["cached_tokens"], 4000)
                    self.assertEqual(log.cache_usage["cache_write_tokens"], 7000)
                    self.assertEqual(log.cache_usage["uncached_input_tokens"], 1000)
                    wire = type(self).cache_bodies[-1]
                    self.assertEqual(wire["model"], "personal-model")
                    self.assertEqual(wire["prompt_cache_key"], "fixture-key")
                    self.assertEqual(wire["prompt_cache_options"], options)
                    self.assertEqual(wire["input" if native else "messages"][0]["content"][0], part)
        self.assertEqual(len(type(self).cache_bodies), 4)
