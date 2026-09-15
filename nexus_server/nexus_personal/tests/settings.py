"""Isolated identity unit-test host, NOT a runnable Community Cloud."""
import sys

if "test" not in sys.argv and "makemigrations" not in sys.argv:
    raise RuntimeError("Identity test settings cannot start a Cloud service.")

SECRET_KEY = "personal-identity-unit-tests-only"
from nexus_personal.composition import *
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.PBKDF2PasswordHasher"]
MIDDLEWARE = [
    "nexus_personal.middleware.PersonalRequestIDMiddleware",
    "nexus_personal.middleware.PersonalContextMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]
ROOT_URLCONF = "nexus_personal.tests.urls"
ALLOWED_HOSTS = ["testserver"]
NEXUS_JWT_ALGORITHM = "HS256"
NEXUS_JWT_ISSUER = "personal-unit-tests"
NEXUS_JWT_AUDIENCE = "personal-unit-tests-client"
NEXUS_JWT_LEEWAY_SECONDS = 0
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["nexus_personal.renderers.PersonalJSONRenderer"],
    "EXCEPTION_HANDLER": "nexus_personal.exceptions.personal_exception_handler",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "nexus_personal.authentication.PersonalBearerAuthentication",
        "nexus_personal.authentication.PersonalSessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
}
