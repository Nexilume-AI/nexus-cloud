from django.core.management.base import BaseCommand, CommandError
from apps.gateway.tasks import refresh_models_dev


class Command(BaseCommand):
    help = "Refresh the validated models.dev snapshot; keep the previous version on failure."

    def handle(self, *args, **options):
        result = refresh_models_dev()
        if result.get("updated") is False:
            raise CommandError("Catalog download or validation failed; previous snapshot retained.")
        self.stdout.write(str(result))
