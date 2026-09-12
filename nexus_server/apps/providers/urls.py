"""Legacy Provider include retaining its name and host-defined order."""
from .http_host import configured_urls

urlpatterns = configured_urls()
