"""Legacy runtime URL include, retaining its host-defined order."""
from .http_host import configured_runtime_urls

urlpatterns = configured_runtime_urls()
