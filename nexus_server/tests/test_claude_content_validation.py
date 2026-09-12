"""Shared Claude conversion guards, runnable in either distribution host."""
from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase, override_settings
from apps.gateway.claude_compat import (
    ClaudeCompatibilityError, ClaudeMessagesView, convert_anthropic_content,
)


class ClaudeContentValidationTests(SimpleTestCase):
    def image(self, url):
        return {"type": "image", "source": {"type": "url", "url": url}}

    def test_valid_text_url_and_base64_keep_compatible_shape(self):
        content = [{"type": "text", "text": "describe"}, self.image("https://example.com/image.png"),
                   {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "aGVsbG8="}}]
        self.assertEqual(convert_anthropic_content(content), [
            {"type": "text", "text": "describe"},
            {"type": "image_url", "image_url": {"url": "https://example.com/image.png"}},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}},
        ])
        self.assertEqual(convert_anthropic_content("plain text"), "plain text")

    def test_invalid_images_never_dispatch_nonstream_or_stream_requests(self):
        for stream in (False, True):
            for url in ("http://127.0.0.1/private", "http://[::1]/private", "http://localhost/private",
                        "file:///private", "data:text/plain;base64,aGVsbG8="):
                with self.subTest(stream=stream, url=url):
                    request = SimpleNamespace(api_key=None, data={"model": "vision", "stream": stream,
                        "messages": [{"role": "user", "content": [self.image(url)]}]})
                    with patch("apps.gateway.claude_compat.chat_completions") as chat, \
                         patch("apps.gateway.claude_compat.anthropic_stream_chunks") as chunks:
                        with self.assertRaises(ClaudeCompatibilityError) as raised:
                            ClaudeMessagesView().post(request)
                        self.assertNotIn(url, str(raised.exception))
                        chat.assert_not_called()
                        chunks.assert_not_called()

    @override_settings(NEXUS_MULTIMODAL_DATA_URL_MAX_BYTES=32)
    def test_base64_and_url_sources_share_image_size_limit(self):
        for source in ({"type": "base64", "media_type": "image/png", "data": "a" * 64},
                       {"type": "url", "url": "data:image/png;base64," + "a" * 64}):
            with self.assertRaises(ClaudeCompatibilityError):
                convert_anthropic_content([{"type": "image", "source": source}])

    @override_settings(NEXUS_MULTIMODAL_MAX_IMAGE_PARTS=1, NEXUS_MULTIMODAL_MAX_CONTENT_PARTS=2)
    def test_image_and_content_count_limits_match_chat(self):
        for blocks in ([self.image("https://example.com/a.png")] * 2,
                       [{"type": "text", "text": "a"}] * 3):
            with self.assertRaises(ClaudeCompatibilityError):
                convert_anthropic_content(blocks)
