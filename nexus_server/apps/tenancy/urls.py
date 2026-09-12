"""Legacy include name with explicit host-selected management routes."""
from .host import configured_urls

urlpatterns = configured_urls()
