"""Legacy runtime-view imports require an explicit process-level host."""
import sys
from .http_host import configured_runtime_views

sys.modules[__name__] = configured_runtime_views()
