import getpass
import sys

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError
from nexus_personal.services import PersonalSetupError, provision_owner, require_personal_distribution


class Command(BaseCommand):
    help = "Initialize the single Community owner locally; never resets an existing installation."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--display-name", default="")
        parser.add_argument("--password-stdin", action="store_true",
                            help="Read one password line from a protected input pipe, never command arguments.")

    def handle(self, *args, **options):
        require_personal_distribution()
        if options["password_stdin"]:
            password = sys.stdin.readline(4098)
            if len(password) > 4096:
                raise CommandError("Password exceeds the input limit.")
            password = password.removesuffix("\n").removesuffix("\r")
        else:
            if not sys.stdin.isatty():
                raise CommandError("Use an interactive terminal or the explicit --password-stdin option.")
            password = getpass.getpass("Owner password: ")
            confirmation = getpass.getpass("Confirm password: ")
            if password != confirmation:
                raise CommandError("Passwords do not match.")
        try:
            provision_owner(email=options["email"], password=password, display_name=options["display_name"])
        except PersonalSetupError as exc:
            raise CommandError(str(exc)) from None
        except DatabaseError:
            raise CommandError("Personal setup could not be saved. Check database availability before retrying.") from None
        finally:
            password = ""
        self.stdout.write("Personal instance initialized. Sign in with the configured owner.")
