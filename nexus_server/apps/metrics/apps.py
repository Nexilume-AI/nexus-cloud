from __future__ import annotations

from django.apps import AppConfig


class MetricsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.metrics"

    def import_models(self):
        super().import_models()
        from apps.common.schema_extension import schema_extension
        schema_extension("NEXUS_MONITORING_MODEL_EXTENSION").register_models()

    def ready(self) -> None:
        from .otel import configure_otel

        configure_otel()
