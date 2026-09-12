"""Host-selected additions to a Run's model and transport context."""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.models import QuerySet
from django.utils.module_loading import import_string


def _backend():
    path = getattr(settings, "NEXUS_AGENT_CONTEXT_EXTENSION_BACKEND", "")
    try:
        backend = import_string(path)()
    except (ImportError, AttributeError, TypeError, ValueError):
        raise ImproperlyConfigured("Agent Run context extension is not configured.") from None
    if not all(callable(getattr(backend, name, None)) for name in ("issue", "expired_model_values", "renewed_model_values", "cleared_model_values", "presentation_fields", "annotate_invocations", "interactor_fields", "close_legacy_browser")):
        raise ImproperlyConfigured("Agent Run context extension is incomplete.")
    return backend


def issue_run_context_extension(*, agent, tool_name, now):
    extension = _backend().issue(agent=agent, tool_name=tool_name, now=now)
    if not isinstance(getattr(extension, "model_values", None), dict) or not callable(getattr(extension, "display_values", None)):
        raise ImproperlyConfigured("Agent Run context extension is invalid.")
    return extension


def expired_run_context_fields(now):
    fields = _backend().expired_model_values(now)
    if not isinstance(fields, dict):
        raise ImproperlyConfigured("Agent Run context expiry fields are invalid.")
    return fields


def renewed_run_context_fields(*, run, expires_at):
    fields = _backend().renewed_model_values(run=run, expires_at=expires_at)
    if not isinstance(fields, dict):
        raise ImproperlyConfigured("Agent Run context renewal fields are invalid.")
    return fields


def cleared_run_context_fields():
    fields = _backend().cleared_model_values()
    if not isinstance(fields, dict):
        raise ImproperlyConfigured("Agent Run context clearing fields are invalid.")
    return fields


def run_presentation_fields(*, invocation):
    """Only the configured host supplies additional non-secret display fields.

    This is presentation, not authorization; callers first resolve their Run
    through the normal subject/context/Display Token checks.
    """
    fields = _backend().presentation_fields(invocation=invocation)
    if not isinstance(fields, dict):
        raise ImproperlyConfigured("Agent Run presentation fields are invalid.")
    return fields


def annotate_run_invocations(*, queryset, latest, summary):
    result = _backend().annotate_invocations(queryset=queryset, latest=latest, summary=summary)
    if not isinstance(result, QuerySet) or result.model is not queryset.model or result.db != queryset.db:
        raise ImproperlyConfigured("Agent Run presentation queryset is invalid.")
    return result


def interactor_presentation_fields(*, agent):
    fields = _backend().interactor_fields(agent=agent)
    if not isinstance(fields, dict):
        raise ImproperlyConfigured("Agent interactor presentation is invalid.")
    return fields


def close_legacy_browser_session(*, run_id):
    return _backend().close_legacy_browser(run_id=run_id)
