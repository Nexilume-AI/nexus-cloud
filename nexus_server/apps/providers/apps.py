from __future__ import annotations

from django.apps import AppConfig


class ProvidersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.providers"

    def import_models(self):
        super().import_models()
        from apps.common.schema_extension import schema_extension
        schema_extension("NEXUS_PROVIDER_MODEL_EXTENSION").register_models()
