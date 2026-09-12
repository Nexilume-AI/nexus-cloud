"""Compatibility module for the explicitly selected monitoring policy."""
import sys
from .policy_host import configured_policy
sys.modules[__name__] = configured_policy()
