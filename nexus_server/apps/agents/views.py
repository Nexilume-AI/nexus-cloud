"""Legacy import path for the explicitly selected Agent HTTP host.

Shared views live in dedicated core modules. A host is required; importing this
compatibility path must never implicitly enable another distribution.
"""
import sys
from .http_host import configured_views

# Keep legacy patches of view-module globals bound to the actual implementation.
sys.modules[__name__] = configured_views()
