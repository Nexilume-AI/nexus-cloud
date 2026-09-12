from types import SimpleNamespace
from urllib.parse import urljoin


class ExecutionRequest(SimpleNamespace):
    """Server-only rehydrated request; never instantiated from HTTP fields."""

    def build_absolute_uri(self, location=None):
        return urljoin(self.base_url, location or self.path)

    def get_host(self):
        from urllib.parse import urlsplit
        return urlsplit(self.base_url).netloc

    def is_secure(self):
        return self.base_url.startswith("https:")
