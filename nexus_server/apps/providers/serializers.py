"""Compatibility import for explicitly selected Provider serializer hosts."""
import sys
from .http_host import configured_serializers

sys.modules[__name__] = configured_serializers()
