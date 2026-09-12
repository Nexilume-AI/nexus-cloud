"""Compatibility imports for the explicit tenancy services host."""
import sys
from .host import configured_module

sys.modules[__name__] = configured_module("services")
