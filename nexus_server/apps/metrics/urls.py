"""Compatibility module bound to an explicit monitoring distribution."""
from .component_host import configured_component
urlpatterns = configured_component("urls").urlpatterns
