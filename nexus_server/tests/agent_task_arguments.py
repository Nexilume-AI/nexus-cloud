"""Restore actual queued invocation arguments without granting test capacity."""
import base64
import json
from apps.agents.runtime_runner import RuntimeDisplayContext
from apps.agents.task_execution import restore_request
from apps.common.crypto import decrypt_secret


def queued_arguments(task):
    payload = json.loads(decrypt_secret(task.execution.encrypted_payload))
    return dict(task_id=str(task.pk), request=restore_request(payload), agent_id=str(task.agent_id),
        body=base64.b64decode(payload["body"]), headers=payload["headers"],
        display_pair=(task.run, RuntimeDisplayContext(**payload["context"])))
