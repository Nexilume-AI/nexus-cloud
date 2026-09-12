"""Shared catalog admission control; independent of Marketplace installation."""
import hashlib

from django.conf import settings
from rest_framework.throttling import SimpleRateThrottle


class CatalogThrottle(SimpleRateThrottle):
    # Keep the existing scope/cache namespace for mixed-version deployments.
    scope = "public_catalog"

    def get_rate(self):
        return getattr(settings, "NEXUS_CATALOG_RATE", getattr(settings, "NEXUS_MARKETPLACE_RATE", "180/min"))

    def get_cache_key(self, request, view):
        principal = str(getattr(request.user, "pk", "") or request.META.get("REMOTE_ADDR", "unknown"))
        digest = hashlib.sha256(principal.encode()).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}
