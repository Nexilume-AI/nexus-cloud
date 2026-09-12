"""Isolated application identity; never loads config.tasks or Cloud schedules."""
import os
from celery import Celery
from django.core.exceptions import ImproperlyConfigured

if os.environ.get("DJANGO_SETTINGS_MODULE", "nexus_personal.settings") != "nexus_personal.settings":
    raise ImproperlyConfigured("Personal workers require nexus_personal.settings.")
os.environ["DJANGO_SETTINGS_MODULE"] = "nexus_personal.settings"
from .worker_composition import TASK_MODULES
app = Celery("nexus_personal", include=list(TASK_MODULES))
app.config_from_object("django.conf:settings", namespace="CELERY")
# Do not autodiscover the mixed tree's tasks or inherit Enterprise Beat schedules.
