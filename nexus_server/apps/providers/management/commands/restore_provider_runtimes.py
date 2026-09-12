from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.providers.runtime_services import restore_provider_runtime_processes


class Command(BaseCommand):
    help = "Restore Provider Runtime containers that should remain available across Cloud or Docker restarts."

    def handle(self, *args, **options):
        result = restore_provider_runtime_processes()
        self.stdout.write(
            self.style.SUCCESS(
                "Provider Runtime reconciliation complete: "
                f"examined={result['examined']} "
                f"restored={result['restored']} "
                f"already_running={result['already_running']} "
                f"failed={result['failed']}"
            )
        )
