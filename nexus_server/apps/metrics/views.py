"""Compatibility module bound to an explicit monitoring distribution."""
import sys
from .component_host import configured_component
sys.modules[__name__] = configured_component("views")
