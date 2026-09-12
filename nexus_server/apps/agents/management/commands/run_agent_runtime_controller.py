from django.core.management.base import BaseCommand

from apps.agents.runtime_controller import (
    AgentRuntimeControllerServer,
    ensure_agent_runtime_controller_dependencies,
)


class Command(BaseCommand):
    help = "Run the isolated Agent Runtime Docker controller."

    def handle(self, *args, **options):
        ensure_agent_runtime_controller_dependencies()
        self.stdout.write("Agent Runtime Controller starting")
        AgentRuntimeControllerServer().serve_forever()
