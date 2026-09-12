from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    name = "apps.notifications"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import signals  # noqa: F401
        from .policy import load_signal_modules
        load_signal_modules()
