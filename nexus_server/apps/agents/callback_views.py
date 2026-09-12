"""Scoped SDK callback HTTP; no commercial configuration or billing views."""
from rest_framework import exceptions, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.workspaces.serializers import (
    WorkspaceConnectionCreateSerializer,
    WorkspaceConnectionUpdateSerializer,
    WorkspaceConnectionValidateSerializer,
)
from .runtime_services import (
    AgentMemoryRevisionConflict,
    list_runtime_workspace_files,
    list_invocation_workspace_files,
    read_invocation_workspace_file,
    write_invocation_workspace_file,
    run_invocation_terminal_command,
    invocation_terminal_status,
    bind_delegate_connection,
    create_run_display_asset,
    create_run_interaction,
    get_run_checkpoint,
    get_run_interaction,
    get_run_context,
    save_run_checkpoint,
    create_delegate_connection,
    delete_delegate_connection,
    get_delegate_connection,
    list_delegate_bindings,
    list_delegate_connections,
    test_delegate_connection,
    update_delegate_connection,
    validate_delegate_connection,
    recall_invocation_memory,
    delete_invocation_memory,
    read_runtime_workspace_file,
    update_invocation_memory,
    run_runtime_workspace_command,
    report_agent_model_usage,
    write_runtime_workspace_file,
)
from .runtime_context import redeem_openwrt_run_context
from .mobile_access import (
    create_mobile_delegate_command,
    mobile_delegate_command,
    mobile_delegate_status,
)
from .browser_runtime import browser_delegate_operation
from .delegate_tokens import interaction_token


def workspace_token(request) -> str:
    return (
        request.headers.get("X-Nexus-Workspace-Token")
        or request.headers.get("X-Nexus-Workspace-Delegate-Token")
        or bearer_token(request.headers.get("Authorization", ""))
    )


def mobile_delegate_token(request) -> str:
    return request.headers.get("X-Nexus-Mobile-Delegate-Token") or ""


def browser_delegate_token(request) -> str:
    return request.headers.get("X-Nexus-Browser-Delegate-Token") or ""


def memory_revision_conflict_response(exc: AgentMemoryRevisionConflict) -> Response:
    return Response(
        {
            "ok": False,
            "data": {},
            "error": {
                "code": exc.default_code,
                "message": str(exc.detail),
                "current_revision": exc.current_revision,
            },
            "request_id": getattr(exc, "request_id", ""),
        },
        status=status.HTTP_409_CONFLICT,
    )


def workspace_delegate_token(request) -> str:
    return (
        request.headers.get("X-Nexus-Workspace-Delegate-Token")
        or request.headers.get("X-Nexus-Workspace-Token")
        or bearer_token(request.headers.get("Authorization", ""))
    )


def bearer_token(value: str) -> str:
    prefix = "Bearer "
    return value[len(prefix) :].strip() if value.startswith(prefix) else ""


class InternalAgentRunContextExchangeView(APIView):
    """Redeem one OpenWrt IPv6 Run Context without exposing caller tokens."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, grant_id):
        token = str(request.headers.get("X-Nexus-Run-Context-Token") or "")
        context = redeem_openwrt_run_context(grant_id=grant_id, token=token)
        if context is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response({"context": context})


class InternalInvocationUsageView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def put(self, request, run_id):
        usage = report_agent_model_usage(
            run_id=str(run_id),
            token=str(request.headers.get("X-Nexus-Interaction-Token") or ""),
            data=request.data if isinstance(request.data, dict) else {},
        )
        return Response({"accepted": True, "event_id": usage.event_id})

    post = put


class InternalAgentWorkspaceFilesView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, runtime_id):
        data = list_runtime_workspace_files(
            runtime_id=str(runtime_id),
            token=workspace_token(request),
            path=request.query_params.get("path") or "",
        )
        return Response(data)


class InternalAgentWorkspaceFileReadView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, runtime_id):
        data = read_runtime_workspace_file(
            runtime_id=str(runtime_id),
            token=workspace_token(request),
            path=request.query_params.get("path") or "",
        )
        return Response(data)


class InternalAgentWorkspaceFileWriteView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, runtime_id):
        data = write_runtime_workspace_file(
            runtime_id=str(runtime_id),
            token=workspace_token(request),
            path=str(request.data.get("path") or ""),
            content=str(request.data.get("content") or ""),
        )
        return Response(data, status=status.HTTP_201_CREATED)


class InternalAgentWorkspaceCommandRunView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, runtime_id):
        data = run_runtime_workspace_command(
            runtime_id=str(runtime_id),
            token=workspace_token(request),
            command=str(request.data.get("command") or ""),
            cwd=str(request.data.get("cwd") or "."),
            timeout_seconds=request.data.get("timeout_seconds"),
        )
        return Response(data)


class InternalInvocationWorkspaceView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, run_id):
        operation = request.query_params.get("operation") or "list"
        kwargs = {
            "run_id": str(run_id),
            "token": workspace_token(request),
            "path": request.query_params.get("path") or "",
            "root_kind": request.query_params.get("root") or "workspace",
        }
        if operation == "read":
            return Response(read_invocation_workspace_file(**kwargs))
        return Response(list_invocation_workspace_files(**kwargs))

    def post(self, request, run_id):
        data = write_invocation_workspace_file(
            run_id=str(run_id),
            token=workspace_token(request),
            path=str(request.data.get("path") or ""),
            content=str(request.data.get("content") or ""),
            root_kind=str(request.data.get("root") or "workspace"),
            idempotency_key=str(request.data.get("idempotency_key") or ""),
        )
        return Response(data, status=status.HTTP_201_CREATED)


class InternalInvocationTerminalView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, run_id):
        return Response(invocation_terminal_status(run_id=str(run_id), token=workspace_token(request)))

    def post(self, request, run_id):
        display = request.data.get("display", True)
        if not isinstance(display, bool):
            raise exceptions.ValidationError({"display": "Must be a boolean."})
        return Response(
            run_invocation_terminal_command(
                run_id=str(run_id),
                token=workspace_token(request),
                command=str(request.data.get("command") or ""),
                cwd=str(request.data.get("cwd") or "."),
                timeout_seconds=request.data.get("timeout_seconds"),
                display=display,
                idempotency_key=str(request.data.get("idempotency_key") or ""),
            )
        )


class InternalInvocationMemoryView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, run_id):
        return Response(
            recall_invocation_memory(
                run_id=str(run_id),
                token=workspace_token(request),
                limit=int(request.query_params.get("limit") or 50),
            )
        )


class InternalInvocationMemoryDetailView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def patch(self, request, run_id, memory_id):
        try:
            data = update_invocation_memory(
                run_id=str(run_id),
                memory_id=str(memory_id),
                token=workspace_token(request),
                data=dict(request.data),
            )
        except AgentMemoryRevisionConflict as exc:
            return memory_revision_conflict_response(exc)
        return Response(data)

    def delete(self, request, run_id, memory_id):
        try:
            data = delete_invocation_memory(
                run_id=str(run_id),
                memory_id=str(memory_id),
                token=workspace_token(request),
                data=dict(request.data),
            )
        except AgentMemoryRevisionConflict as exc:
            return memory_revision_conflict_response(exc)
        return Response(data)


class InternalInvocationConnectionListView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, run_id):
        return Response(list_delegate_connections(run_id=str(run_id), token=workspace_delegate_token(request)))

    def post(self, request, run_id):
        serializer = WorkspaceConnectionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            create_delegate_connection(
                run_id=str(run_id),
                token=workspace_delegate_token(request),
                data=serializer.validated_data,
            ),
            status=status.HTTP_201_CREATED,
        )


class InternalInvocationConnectionDetailView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, run_id, connection_id):
        return Response(get_delegate_connection(
            run_id=str(run_id),
            token=workspace_delegate_token(request),
            connection_id=str(connection_id),
        ))

    def patch(self, request, run_id, connection_id):
        serializer = WorkspaceConnectionUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        return Response(update_delegate_connection(
            run_id=str(run_id),
            token=workspace_delegate_token(request),
            connection_id=str(connection_id),
            data=serializer.validated_data,
        ))

    def delete(self, request, run_id, connection_id):
        delete_delegate_connection(
            run_id=str(run_id),
            token=workspace_delegate_token(request),
            connection_id=str(connection_id),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class InternalInvocationConnectionTestView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, run_id, connection_id):
        return Response(test_delegate_connection(
            run_id=str(run_id),
            token=workspace_delegate_token(request),
            connection_id=str(connection_id),
        ))


class InternalInvocationConnectionValidateView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, run_id):
        serializer = WorkspaceConnectionValidateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(validate_delegate_connection(
            run_id=str(run_id),
            token=workspace_delegate_token(request),
            data=serializer.validated_data,
        ))


class InternalInvocationBindingView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, run_id):
        return Response(list_delegate_bindings(run_id=str(run_id), token=workspace_delegate_token(request)))

    def post(self, request, run_id):
        connection_id = str(request.data.get("connection_id") or "")
        if not connection_id:
            raise exceptions.ValidationError({"connection_id": "This field is required."})
        return Response(bind_delegate_connection(
            run_id=str(run_id),
            token=workspace_delegate_token(request),
            connection_id=connection_id,
            make_default=bool(request.data.get("make_default", True)),
        ))


class InternalInvocationMobileView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, run_id):
        return Response(mobile_delegate_status(run_id=str(run_id), token=mobile_delegate_token(request)))

    def post(self, request, run_id):
        return Response(
            create_mobile_delegate_command(
                run_id=str(run_id),
                token=mobile_delegate_token(request),
                data=dict(request.data),
            ),
            status=status.HTTP_201_CREATED,
        )


class InternalInvocationBrowserView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, run_id):
        return Response(
            browser_delegate_operation(
                run_id=str(run_id),
                token=browser_delegate_token(request),
                data=dict(request.data),
            )
        )


class InternalInvocationMobileCommandView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, run_id, command_id):
        return Response(
            mobile_delegate_command(
                run_id=str(run_id),
                command_id=str(command_id),
                token=mobile_delegate_token(request),
            )
        )


class InternalRunInteractionListView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, run_id):
        return Response(
            create_run_interaction(
                run_id=str(run_id),
                token=interaction_token(request),
                data=dict(request.data),
            ),
            status=status.HTTP_201_CREATED,
        )


class InternalRunInteractionDetailView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, run_id, interaction_id):
        return Response(
            get_run_interaction(
                run_id=str(run_id),
                interaction_id=str(interaction_id),
                token=interaction_token(request),
            )
        )


class InternalRunContextView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, run_id):
        return Response(
            get_run_context(
                run_id=str(run_id),
                token=str(request.headers.get("X-Nexus-Context-Token") or ""),
                limit=request.query_params.get("limit", 200),
            )
        )


class InternalRunControlView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, run_id):
        from .task_execution import execution_control
        return Response(execution_control(str(run_id), interaction_token(request)))


class InternalRunCheckpointView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, run_id):
        return Response(get_run_checkpoint(run_id=str(run_id), token=interaction_token(request)))

    def put(self, request, run_id):
        return Response(
            save_run_checkpoint(
                run_id=str(run_id),
                token=interaction_token(request),
                data=dict(request.data),
            )
        )


class InternalRunOperationView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, run_id, operation_id=None):
        from .task_execution import finish_operation, prepare_operation

        token = interaction_token(request)
        if operation_id is None:
            return Response(prepare_operation(str(run_id), token, dict(request.data)), status=status.HTTP_201_CREATED)
        return Response(finish_operation(str(run_id), str(operation_id), token, dict(request.data)))


class InternalRunDisplayAssetView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, run_id, asset_id=None):
        from django.http import FileResponse
        from rest_framework.exceptions import MethodNotAllowed
        from .runtime_services import get_internal_interaction_run, AgentRuntimeNotFound
        if asset_id is None:
            raise MethodNotAllowed("GET")
        run = get_internal_interaction_run(run_id=str(run_id), token=interaction_token(request))
        asset = run.display_assets.filter(id=asset_id).first()
        if asset is None:
            raise AgentRuntimeNotFound("Image not found.")
        try:
            response = FileResponse(asset.file.open("rb"), content_type=asset.content_type)
        except FileNotFoundError:
            raise AgentRuntimeNotFound("Image not found.") from None
        response["Cache-Control"] = "no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    def post(self, request, run_id, asset_id=None):
        if asset_id is not None:
            from rest_framework.exceptions import MethodNotAllowed
            raise MethodNotAllowed("POST")
        return Response(
            create_run_display_asset(
                run_id=str(run_id),
                token=interaction_token(request),
                data=dict(request.data),
            ),
            status=status.HTTP_201_CREATED,
        )
