from django.apps import AppConfig


class PersonalConfig(AppConfig):
    name = "nexus_personal"
    label = "personal"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from .tool_credentials import register_lifecycle_receivers
        register_lifecycle_receivers()
