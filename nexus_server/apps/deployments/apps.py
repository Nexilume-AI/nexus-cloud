from __future__ import annotations

from django.apps import AppConfig


class DeploymentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.deployments"

    def import_models(self):
        super().import_models()
        from apps.common.schema_extension import schema_extension
        schema_extension("NEXUS_DEPLOYMENT_MODEL_EXTENSION").register_models()
