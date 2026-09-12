"""Legacy Provider views require explicit process-level composition."""
import sys
from .http_host import configured_views

sys.modules[__name__] = configured_views()
