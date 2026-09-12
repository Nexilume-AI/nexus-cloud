"""Spawn-safe entry point. Only task/lease IDs cross the process boundary."""


def run_task_process(task_id, lease_id):
    import multiprocessing
    import os
    import threading
    import time

    def watch_parent():
        parent = multiprocessing.parent_process()
        while parent is not None:
            time.sleep(5)
            if not parent.is_alive():
                # Never keep an orphaned local execution transport alive. The
                # database lease still fences any remote Agent side effects.
                os._exit(71)

    threading.Thread(target=watch_parent, daemon=True).start()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()
    from .task_execution import execute
    execute(task_id, lease_id)
