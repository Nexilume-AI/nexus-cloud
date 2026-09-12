from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "Fail closed unless the configured PostgreSQL database is reachable."

    def handle(self, *args, **options):
        try:
            if connection.vendor != "postgresql":
                raise RuntimeError()
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_database(), current_user, current_setting('server_version')")
                database, role, version = cursor.fetchone()
        except Exception:
            raise CommandError("PostgreSQL is required and must be reachable; SQLite fallback is disabled.") from None
        self.stdout.write(f"PostgreSQL ready: database={database} role={role} version={version}")
