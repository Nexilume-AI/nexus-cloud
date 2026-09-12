"""Bounded raster decoding and DNS-pinned, redirect-free public image fetches."""
import base64
import binascii
import http.client
import ipaddress
import socket
import ssl
import warnings
from io import BytesIO
from urllib.parse import urlsplit

from django.conf import settings
from PIL import Image, UnidentifiedImageError
from rest_framework.exceptions import ValidationError

MIME = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def max_image_bytes():
    return min(int(getattr(settings, "NEXUS_IMAGE_MAX_BYTES", 10 * 1024 * 1024)), 25 * 1024 * 1024)


def validate_image(data, declared_type=None):
    if not isinstance(data, bytes) or not 0 < len(data) <= max_image_bytes():
        raise ValidationError("Image is empty or exceeds the image byte limit.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as img:
                mime = MIME.get(img.format)
                if not mime or getattr(img, "n_frames", 1) != 1:
                    raise ValueError("Only still PNG, JPEG and WebP images are supported.")
                pixels = int(getattr(settings, "NEXUS_IMAGE_MAX_PIXELS", 24_000_000))
                if img.width * img.height > pixels or min(img.size) < 1:
                    raise ValueError("Image exceeds the pixel limit.")
                dimensions = img.size
                img.verify()
            # verify() alone does not decode all formats (notably JPEG).
            with Image.open(BytesIO(data)) as img:
                img.load()
        if declared_type and declared_type.split(";", 1)[0].lower() != mime:
            raise ValueError("Image bytes do not match the declared MIME type.")
    except (OSError, ValueError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValidationError("Invalid image: only bounded, valid PNG, JPEG or WebP raster data is accepted.") from exc
    return mime, dimensions


def decode_image_base64(value):
    if not isinstance(value, str) or len(value) > (max_image_bytes() + 2) // 3 * 4:
        raise ValidationError("Image base64 exceeds the byte limit.")
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValidationError("Invalid image base64.") from exc
    validate_image(data)
    return data


def fetch_public_image(url):
    """No proxy, cookies, redirects or DNS second lookup; TLS keeps hostname checks."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError()
        port = parsed.port or 443
        if port != 443:
            raise ValueError()
        addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
            raise ValueError()
    except (ValueError, OSError) as exc:
        raise ValidationError("Image URL must resolve exclusively to public HTTPS addresses on port 443.") from exc
    connection = http.client.HTTPSConnection(parsed.hostname, port, timeout=20, context=ssl.create_default_context())
    raw_socket = None
    try:
        family, socktype, proto, _, address = addresses[0]
        raw_socket = socket.socket(family, socktype, proto)
        raw_socket.settimeout(20)
        raw_socket.connect(address)
        connection.sock = ssl.create_default_context().wrap_socket(raw_socket, server_hostname=parsed.hostname)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        connection.request("GET", path, headers={"Accept": "image/png,image/jpeg,image/webp", "Connection": "close"})
        response = connection.getresponse()
        if response.status != 200:
            raise ValidationError("Image URL was rejected; redirects are not followed.")
        data = response.read(max_image_bytes() + 1)
        validate_image(data, response.getheader("Content-Type"))
        return data
    except (OSError, http.client.HTTPException) as exc:
        raise ValidationError("Image could not be fetched securely.") from exc
    finally:
        connection.close()
        if raw_socket is not None:
            raw_socket.close()
