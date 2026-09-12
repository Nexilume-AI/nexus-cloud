"""Load explicitly selected production settings without importing config.asgi."""
import os
from django.core.exceptions import ImproperlyConfigured

if os.environ.get("DJANGO_SETTINGS_MODULE", "nexus_personal.settings") != "nexus_personal.settings":
    raise ImproperlyConfigured("Personal ASGI entry requires nexus_personal.settings.")
os.environ["DJANGO_SETTINGS_MODULE"] = "nexus_personal.settings"

from .celery import app as celery_app
from .application import create_application

application = create_application()
