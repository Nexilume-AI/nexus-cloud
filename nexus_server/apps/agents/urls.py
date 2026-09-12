"""Legacy URL include, retaining its name and host-defined ordering."""
from .http_host import configured_urls

urlpatterns = configured_urls()
