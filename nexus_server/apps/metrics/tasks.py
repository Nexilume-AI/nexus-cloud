"""Legacy task imports select the installed distribution, not report code."""
import sys
from .component_host import configured_component
sys.modules[__name__] = configured_component("tasks")
