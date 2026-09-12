"""Required host policy for job reading; never grants general admin rights."""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


def job_access():
    try:
        backend = import_string(getattr(settings, 'NEXUS_JOB_ACCESS_BACKEND', ''))()
    except (ImportError, AttributeError, TypeError, ValueError):
        raise ImproperlyConfigured('Job access backend is not configured.') from None
    if not all(callable(getattr(backend, name, None)) for name in ('require_read', 'scope')):
        raise ImproperlyConfigured('Job access backend is incomplete.')
    return backend
