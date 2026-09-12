from django.core.management.base import BaseCommand

from apps.providers.provider_controller import (
    ProviderRuntimeControllerServer,
    ensure_provider_runtime_controller_dependencies,
)


class Command(BaseCommand):
    help = "Run the isolated Provider Runtime Docker controller."

    def handle(self, *args, **options):
        ensure_provider_runtime_controller_dependencies()
        self.stdout.write("Provider Runtime Controller starting")
        ProviderRuntimeControllerServer().serve_forever()
