"""Dedicated durable PostgreSQL Agent worker; safe to run on multiple hosts."""
import signal
import threading
import time
import multiprocessing

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections, connection
from apps.agents.task_execution import claim, heartbeat, sweep, terminate
from apps.agents.task_process import run_task_process
from apps.agents.models import AgentTaskExecution
from apps.agents.follow_ups import dispatch_pending
from apps.common.runtime_health import record_agent_worker_heartbeat


class Command(BaseCommand):
    help = "Dispatch durable Agent calls with PostgreSQL leases and stale-run recovery."

    def add_arguments(self, parser):
        parser.add_argument("--concurrency", type=int, default=4)
        parser.add_argument("--once", action="store_true", help="Sweep and process currently available jobs, then exit.")

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            raise CommandError("Agent task workers require PostgreSQL.")
        stop = threading.Event()
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda *_: stop.set())
        size = max(1, min(options["concurrency"], 32))
        active = {}
        next_heartbeat = 0.0
        # A process supervisor may terminate after its grace period. In-flight
        # leases then expire; another worker fences unknown outcomes, not replay.
        # Each call owns an OS process. A stuck transport cannot permanently
        # exhaust the worker's slots after its deadline/cancellation grace.
        context = multiprocessing.get_context("spawn")
        try:
            while not stop.is_set() or active:
                close_old_connections()
                try:
                    if time.monotonic() >= next_heartbeat:
                        record_agent_worker_heartbeat()
                        sweep()
                        for process, (task_id, lease_id) in list(active.items()):
                            if not process.is_alive():
                                process.join()
                                active.pop(process)
                                continue
                            try:
                                if not heartbeat(task_id, lease_id):
                                    if not AgentTaskExecution.objects.filter(task_id=task_id, lease_id=lease_id, state="running").exists():
                                        process.terminate()
                            except Exception:
                                terminate(task_id, "TASK_AUTHORITY_UNAVAILABLE", lease_id=lease_id)
                        next_heartbeat = time.monotonic() + 15
                    for process in list(active):
                        if not process.is_alive():
                            process.join()
                            active.pop(process)
                    while not stop.is_set() and len(active) < size:
                        dispatch_pending()
                        lease = claim()
                        if lease is None:
                            break
                        process = context.Process(target=run_task_process, args=lease)
                        process.start()
                        active[process] = lease
                    if options["once"] and not active:
                        break
                except Exception:
                    # Database outage: never extend leases optimistically or log
                    # exception text which could include connection credentials.
                    self.stderr.write("Agent worker storage unavailable; retrying safely.")
                time.sleep(1)
        finally:
            for process in active:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=5)
