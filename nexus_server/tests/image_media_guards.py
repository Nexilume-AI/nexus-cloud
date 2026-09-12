"""Portable raster safety and signed-image HTTP assertions; no financial fixture."""
import base64
from io import BytesIO
from unittest.mock import patch
from django.test import override_settings
from PIL import Image
from rest_framework.exceptions import ValidationError
from apps.gateway.image_media import validate_image, fetch_public_image


def png():
    stream = BytesIO()
    Image.new("RGB", (4, 4), "red").save(stream, "PNG")
    return stream.getvalue()


class ImageSafetyGuards:
    def test_png_checksum_error_is_a_validation_failure(self):
        broken = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a4i0AAAAASUVORK5CYII=")
        with self.assertRaises(ValidationError):
            validate_image(broken, "image/png")

    def test_raster_validation(self):
        self.assertEqual(validate_image(png()), ("image/png", (4, 4)))
        for data, mime in [(b"<svg></svg>", "image/svg+xml"), (png(), "image/jpeg"), (png()[:32], "image/png")]:
            with self.subTest(mime=mime), self.assertRaises(ValidationError):
                validate_image(data, mime)

    @override_settings(NEXUS_IMAGE_MAX_PIXELS=8)
    def test_pixel_limit(self):
        with self.assertRaises(ValidationError):
            validate_image(png())

    def test_url_protocol_credentials_ports_and_private_dns_rejected(self):
        for url in ["file:///etc/passwd", "http://example.com/a.png", "https://user:password@example.com/a", "https://example.com:8443/a", "https://127.0.0.1/a"]:
            with self.subTest(url=url), self.assertRaises(ValidationError):
                fetch_public_image(url)
        with patch("apps.gateway.image_media.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.1", 443))]), self.assertRaises(ValidationError):
            fetch_public_image("https://public-looking.example/a.png")


class GatewayImageHTTPGuards:
    @override_settings(NEXUS_PUBLIC_BASE_URL="https://cloud.example:28443")
    def test_signed_image_url_uses_public_origin_not_backend_or_forwarded_host(self):
        response = self.call()
        self.assertEqual(response.status_code, 200, response.content)
        url = response.json()["data"][0]["url"]
        self.assertTrue(url.startswith("https://cloud.example:28443/api/v1/media/assets/"))
        self.assertNotIn("testserver", url)
        from urllib.parse import urlsplit
        parsed = urlsplit(url)
        download = self.client.get(parsed.path + "?" + parsed.query, HTTP_X_FORWARDED_HOST="attacker.example")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(b"".join(download.streaming_content), png())

    def test_unknown_stream_parameter_rejected_without_dispatch(self):
        self.assertEqual(self.call(stream=True).status_code, 400)
        self.assertEqual(len(self.image_requests), 0)
