from __future__ import annotations

from django.apps import AppConfig


class DatasetsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.datasets"

    def import_models(self):
        super().import_models()
        from .model_extension import model_extension
        model_extension().register_models()
