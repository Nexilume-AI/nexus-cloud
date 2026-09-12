"""Load Agent integration only when an Agent operation is requested.

These are dispatchers to the real implementation, not replacement services.
Personal invocation-snapshot archives use explicit owner/source policy. Legacy
deployment workspace reads still require the complete Workspace integration.
"""
from __future__ import annotations

from .models import DatasetFile


def export_agent_trace_to_dataset(*, request, dataset_id: str, agent_id: str, run_id: str) -> DatasetFile:
    from .agent_asset_services import export_agent_trace_to_dataset as implementation
    return implementation(request=request, dataset_id=dataset_id, agent_id=agent_id, run_id=run_id)


def export_agent_memory_to_dataset(*, request, dataset_id: str, agent_id: str, memory_item_ids: list[str] | None = None) -> DatasetFile:
    from .agent_asset_services import export_agent_memory_to_dataset as implementation
    return implementation(request=request, dataset_id=dataset_id, agent_id=agent_id, memory_item_ids=memory_item_ids)


def capture_agent_artifact_to_dataset(*, request, dataset_id: str, agent_id: str, artifact_id: str) -> DatasetFile:
    from .agent_asset_services import capture_agent_artifact_to_dataset as implementation
    return implementation(request=request, dataset_id=dataset_id, agent_id=agent_id, artifact_id=artifact_id)
