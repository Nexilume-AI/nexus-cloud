import os
import threading
import time
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections, connection
from apps.agents.python_builds import claim_build, execute_build
from apps.agents.python_builder import BuildFailure, ensure_builder_dependencies, remove_own_container
from apps.agents.models import AgentPythonBuild
from apps.agents.python_build_cleanup import cleanup_deleted_builds
from apps.common.runtime_health import record_agent_builder_heartbeat


def _heartbeat_loop(stop, stderr):
    while not stop.wait(10):
        try:
            record_agent_builder_heartbeat()
        except Exception:
            # A transient local write/cache failure must not kill the heartbeat
            # thread forever. Never log raw paths or storage exception details.
            stderr.write("Builder heartbeat could not be published; retrying.")


class Command(BaseCommand):
    help = "Dedicated Python build worker (PostgreSQL queue; never run in a web process)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            raise CommandError("Python builds require PostgreSQL.")
        if not settings.NEXUS_AGENT_PYTHON_BUILDS_ENABLED:
            raise CommandError("Python source builds are not enabled.")
        try:
            ensure_builder_dependencies()
        except BuildFailure as exc:
            raise CommandError(f"{exc.code}: {exc.message}") from None
        worker_id = f"{settings.NEXUS_AGENT_RUNTIME_HOST_ID}:{os.getpid()}"
        heartbeat_stop = threading.Event()

        record_agent_builder_heartbeat()
        heartbeat = threading.Thread(target=_heartbeat_loop, args=(heartbeat_stop, self.stderr),
                                     name="agent-builder-heartbeat", daemon=True)
        heartbeat.start()
        try:
            while True:
                close_old_connections()
                build = claim_build(worker_id)
                for expired in AgentPythonBuild.objects.filter(status="failed", error_code="BUILD_WORKER_LOST", target_host=settings.NEXUS_AGENT_RUNTIME_HOST_ID).order_by("-completed_at")[:20]:
                    remove_own_container(f"nexus-python-{expired.id.hex}")
                if build:
                    execute_build(build)
                cleanup_deleted_builds(limit=2)
                if options["once"]:
                    return
                if not build:
                    time.sleep(2)
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=5)
