"""python -m nexus_personal.manage <command>: explicit personal administration."""
import os
import sys


def main():
    from django.core.exceptions import ImproperlyConfigured
    if os.environ.get("DJANGO_SETTINGS_MODULE", "nexus_personal.settings") != "nexus_personal.settings":
        raise ImproperlyConfigured("Personal management requires nexus_personal.settings.")
    for index, argument in enumerate(sys.argv[1:], start=1):
        value = argument.partition('=')[2] if argument.startswith('--settings=') else (
            sys.argv[index + 1] if argument == '--settings' and index + 1 < len(sys.argv) else None)
        if value is not None and value != 'nexus_personal.settings':
            raise ImproperlyConfigured("Personal management refuses a different --settings module.")
    if 'runserver' in sys.argv[1:]:
        raise ImproperlyConfigured("Use the personal ASGI application behind a trusted HTTPS proxy, not runserver.")
    os.environ["DJANGO_SETTINGS_MODULE"] = "nexus_personal.settings"
    from .celery import app  # Ensure shared_task dispatch cannot select the Cloud app.
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
