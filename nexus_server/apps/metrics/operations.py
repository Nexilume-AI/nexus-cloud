"""Explicitly selected monitoring workers, not an implicit financial worker."""
import sys
from .worker_host import configured_workers

sys.modules[__name__] = configured_workers()
