from __future__ import annotations

import posixpath

from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.models import SoftDeleteModel
from apps.common.subjects import request_subject
from apps.common.request_context import get_tenant_from_request
from apps.workspaces.execution import list_workspace_files, read_workspace_file, resolve_workspace_relative_path

from .file_transfers import FileChangedDuringImport, metadata, snapshot_computer_text
from .models import AgentComputerBinding
from .runtime_services import get_runtime_use_agent
from .workspace_grants import effective_workspace_capabilities


def _computer_source(request, agent_id):
    tenant = get_tenant_from_request(request)
    agent = get_runtime_use_agent(request=request, tenant=tenant, agent_id=str(agent_id))
    scopes = set(effective_workspace_capabilities(request=request, agent=agent))
    if not {"files.list", "files.read"}.issubset(scopes):
        raise exceptions.PermissionDenied("Computer file access has not been granted for this Agent.")
    subject = request_subject(request)
    binding = (
        AgentComputerBinding.objects.filter(
            tenant=tenant,
            project_id=getattr(request, "project_id", None) or None,
            agent=agent,
            caller_subject_hash=subject.subject_hash,
            is_default=True,
            status=SoftDeleteModel.STATUS_ACTIVE,
            connection__owner_subject_hash=subject.subject_hash,
            connection__status=SoftDeleteModel.STATUS_ACTIVE,
        )
        .select_related("connection")
        .first()
    )
    if binding is None:
        raise exceptions.NotFound("Caller Computer binding not found.")
    base_root = str(binding.connection.workspace_root or "~/.nexus").rstrip("/")
    return agent, binding, posixpath.join(base_root, "agents", str(agent.id), "workspace")


class AgentComputerFilesView(APIView):
    def get(self, request, agent_id):
        _agent, binding, root = _computer_source(request, agent_id)
        path = resolve_workspace_relative_path(path=str(request.query_params.get("path") or "."))
        query = str(request.query_params.get("q") or "").strip().casefold()
        listing = list_workspace_files(
            connection=binding.connection,
            root=root,
            path=path,
        )
        items = []
        for raw in listing.get("items", []):
            name = str(raw.get("name") or "")
            if query and query not in name.casefold():
                continue
            items.append({
                "name": name,
                "path": str(raw.get("relative_path") or raw.get("path") or name),
                "type": str(raw.get("type") or "file"),
                "size_bytes": int(raw.get("size_bytes") or 0),
                "modified_at": raw.get("modified_at"),
            })
            if len(items) >= 100:
                break
        return Response({"path": path, "items": items})


class AgentComputerFileImportView(APIView):
    def post(self, request, agent_id):
        agent, binding, root = _computer_source(request, agent_id)
        path = resolve_workspace_relative_path(path=str(request.data.get("path") or ""))
        if path == ".":
            raise exceptions.ValidationError({"path": "Select a file."})
        before = read_workspace_file(
            connection=binding.connection,
            root=root,
            path=path,
        )
        expected_size = request.data.get("expected_size_bytes")
        try:
            expected_size = int(expected_size) if expected_size is not None else None
        except (TypeError, ValueError):
            raise exceptions.ValidationError({"expected_size_bytes": "Enter a valid non-negative byte count."}) from None
        if expected_size is not None and expected_size < 0:
            raise exceptions.ValidationError({"expected_size_bytes": "Enter a valid non-negative byte count."})
        if expected_size is not None and expected_size != int(before.get("size_bytes") or 0):
            raise FileChangedDuringImport()
        after = read_workspace_file(
            connection=binding.connection,
            root=root,
            path=path,
        )
        if before.get("content") != after.get("content") or before.get("size_bytes") != after.get("size_bytes"):
            raise FileChangedDuringImport()
        row = snapshot_computer_text(
            request=request,
            agent=agent,
            relative_path=path,
            content=str(after.get("content") or ""),
            source_size=int(after.get("size_bytes") or 0),
        )
        return Response(metadata(row), status=status.HTTP_201_CREATED)
