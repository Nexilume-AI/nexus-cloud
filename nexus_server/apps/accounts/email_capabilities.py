from django.conf import settings


def email_auth_available():
    if not getattr(settings, 'NEXUS_EMAIL_SIGNUP_ENABLED', False):
        return False
    if settings.EMAIL_BACKEND != 'django.core.mail.backends.smtp.EmailBackend':
        return bool(getattr(settings, 'NEXUS_EMAIL_AUTH_ALLOW_TEST_BACKEND', False) and not settings.NEXUS_PRODUCTION)
    return bool(settings.EMAIL_HOST and settings.DEFAULT_FROM_EMAIL
                and not settings.DEFAULT_FROM_EMAIL.endswith('@nexus.local'))
