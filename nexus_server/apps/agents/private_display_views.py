"""Caller-bound Private Display views, without Marketplace or pricing views."""
from django.http import FileResponse
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.datasets.downloads import DownloadCookieAuthentication
from .serializers import AgentOutputArtifactSerializer
from .services import (
    get_private_display_run, private_display_payload, update_private_display_title,
    hide_private_display_run, private_display_events, answer_private_run_interaction,
    get_private_display_asset,
)
from .runtime_services import serialize_private_run


class PrivateAgentRunDisplayView(APIView):
    def get(self, request, run_id):
        run = get_private_display_run(request=request, run_id=str(run_id))
        return Response(private_display_payload(run=run))

    def patch(self, request, run_id):
        run = update_private_display_title(
            request=request,
            run_id=str(run_id),
            title=str(request.data.get("title") or ""),
        )
        return Response(serialize_private_run(run))

    def delete(self, request, run_id):
        hide_private_display_run(request=request, run_id=str(run_id))
        return Response(status=status.HTTP_204_NO_CONTENT)


class PrivateAgentRunEventsView(APIView):
    def get(self, request, run_id):
        run = get_private_display_run(request=request, run_id=str(run_id))
        cursor = int(request.query_params.get("cursor") or 0)
        limit = int(request.query_params.get("limit") or 100)
        return Response(private_display_events(run=run, cursor=cursor, limit=limit))


class PrivateAgentRunOutputsView(APIView):
    def get(self, request, run_id):
        run = get_private_display_run(request=request, run_id=str(run_id))
        outputs = run.output_artifacts.exclude(status="deleted").order_by("workspace_path")
        if request.query_params.get("paged") == "1":
            from .file_directory import directory_page
            return Response(directory_page(request, run, outputs, "output",
                lambda row: AgentOutputArtifactSerializer(row).data))
        return Response(AgentOutputArtifactSerializer(outputs, many=True).data)


class PrivateAgentRunOutputDownloadView(APIView):
    authentication_classes = [DownloadCookieAuthentication, *APIView.authentication_classes]

    def get(self, request, run_id, artifact_id):
        from .file_views import stream_file

        run = get_private_display_run(request=request, run_id=str(run_id), require_token=not bool(getattr(request, "dataset_download_id", None)))
        artifact = (
            run.output_artifacts.exclude(status="deleted")
            .filter(id=artifact_id, snapshot_status="ready")
            .first()
        )
        if artifact is None or not artifact.snapshot_object_key:
            raise exceptions.NotFound("Agent output snapshot not found.")
        return stream_file(request, backend=artifact.snapshot_storage_backend, key=artifact.snapshot_object_key,
            name=artifact.original_file_name, size=artifact.size_bytes, sha256=artifact.sha256)

    post = get
    head = get


class PrivateAgentRunTerminalView(APIView):
    def get(self, request, run_id):
        run = get_private_display_run(request=request, run_id=str(run_id))
        session = getattr(run, "terminal_session", None)
        if session is None:
            return Response(
                {
                    "status": "not_started",
                    "viewer_mode": "read_only",
                    "events": [],
                    "latest_seq": 0,
                    "next_cursor": None,
                    "has_more": False,
                    "truncated": False,
                    "computer_name": "",
                    "shell": "",
                    "workspace_cwd": run.workspace_cwd or ".",
                    "last_error": "",
                    "started_at": None,
                    "ended_at": None,
                }
            )
        cursor = max(int(request.query_params.get("cursor") or 0), 0)
        page = list(session.transcript.filter(seq__gt=cursor).order_by("seq")[:501])
        has_more = len(page) > 500
        rows = page[:500]
        next_cursor = rows[-1].seq if has_more and rows else None
        latest_seq = session.transcript.order_by("-seq").values_list("seq", flat=True).first() or 0
        truncated = session.transcript.filter(
            kind="system",
            data="[terminal transcript truncated]",
        ).exists()
        return Response(
            {
                "status": session.status,
                "viewer_mode": "read_only",
                "latest_seq": latest_seq,
                "next_cursor": next_cursor,
                "has_more": has_more,
                "truncated": truncated,
                "computer_name": session.connection.name,
                "shell": session.shell,
                "workspace_cwd": run.workspace_cwd or ".",
                "last_error": session.last_error,
                "started_at": session.started_at,
                "ended_at": session.ended_at,
                "events": [
                    {
                        "seq": row.seq,
                        "kind": row.kind,
                        "command_id": row.command_id,
                        "data": row.data,
                        "exit_code": row.exit_code,
                        "created_at": row.created_at,
                    }
                    for row in rows
                ],
            }
        )


class PrivateAgentRunInteractionView(APIView):
    def post(self, request, run_id, interaction_id):
        return Response(
            answer_private_run_interaction(
                request=request,
                run_id=str(run_id),
                interaction_id=str(interaction_id),
                data=dict(request.data),
            )
        )


class PrivateAgentRunDisplayAssetView(APIView):
    def get(self, request, run_id, asset_id):
        asset = get_private_display_asset(
            request=request,
            run_id=str(run_id),
            asset_id=str(asset_id),
        )
        return FileResponse(
            asset.file.open("rb"),
            content_type=asset.content_type,
            filename="browser-frame",
        )
