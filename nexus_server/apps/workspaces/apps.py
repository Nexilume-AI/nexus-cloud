from __future__ import annotations

from django.apps import AppConfig


class WorkspacesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.workspaces"

    def import_models(self):
        super().import_models()
        from .model_composition import load_integration_models
        load_integration_models()
