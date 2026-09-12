"""Shared MCP transport views; distribution policy supplies invocation access.

The legacy funding exception name is only a wire-compatibility boundary: this
module imports no wallet, APIKey, IAM, Marketplace or commercial model.
"""
from __future__ import annotations
import json
from django.http import HttpResponse, StreamingHttpResponse
from rest_framework import exceptions
from rest_framework.views import APIView
from rest_framework.renderers import JSONRenderer
from apps.common.renderers import EventStreamRenderer
from apps.common.invocation_lifecycle import InvocationFundingDenied as BalanceNotEnough
from .runtime_services import (
    AgentPolicyDenied, AgentRuntimeError, AgentStreamingNotSupported,
    call_runtime_mcp, call_runtime_mcp_stream, maybe_handle_mcp_task,
)

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

MCP_RESPONSE_HEADERS = {
    "cache-control",
    "content-type",
    "mcp-protocol-version",
    "mcp-session-id",
    "retry-after",
    "x-nexus-agent-display-token",
    "x-nexus-agent-display-url",
    "x-nexus-agent-run-id",
}

def safe_mcp_response_headers(headers: dict[str, str]) -> dict[str, str]:
    connection_tokens = {
        token.strip().lower()
        for key, value in headers.items()
        if key.lower() == "connection"
        for token in value.split(",")
        if token.strip()
    }
    blocked = HOP_BY_HOP_HEADERS | connection_tokens
    return {
        key: value
        for key, value in headers.items()
        if key.lower() in MCP_RESPONSE_HEADERS and key.lower() not in blocked
    }

class AgentMCPProxyView(APIView):
    renderer_classes = [JSONRenderer, EventStreamRenderer]
    upstream_path = ""

    def get(self, request, agent_id):
        return self._proxy(request, agent_id, method="GET")

    def post(self, request, agent_id):
        return self._proxy(request, agent_id, method="POST")

    def delete(self, request, agent_id):
        return self._proxy(request, agent_id, method="DELETE")

    def _proxy(self, request, agent_id, method: str):
        headers = {key[5:].replace("_", "-"): value for key, value in request.META.items() if key.startswith("HTTP_")}
        try:
            task_result = maybe_handle_mcp_task(
                request=request,
                agent_id=str(agent_id),
                method=method,
                body=request.body,
                headers=headers,
            )
        except BalanceNotEnough:
            return mcp_error_response(-32001, "Wallet balance is not enough.", status_code=400)
        except AgentPolicyDenied:
            return mcp_error_response(-32003, "API key policy does not allow this agent.", status_code=403)
        except exceptions.PermissionDenied:
            return mcp_error_response(-32003, "Agent use permission is required.", status_code=403)
        except AgentRuntimeError as exc:
            return mcp_error_response(-32000, str(exc.detail), status_code=exc.status_code)
        if task_result is not None:
            response = HttpResponse(task_result.body, status=task_result.status_code)
            for key, value in safe_mcp_response_headers(task_result.headers).items():
                response[key] = value
            return response
        if wants_event_stream(headers):
            return self._stream_proxy(request, agent_id, method=method, headers=headers)
        try:
            result = call_runtime_mcp(
                request=request,
                agent_id=agent_id,
                method=method,
                body=request.body,
                headers=headers,
                upstream_path=self.upstream_path,
            )
        except BalanceNotEnough:
            return mcp_error_response(-32001, "Wallet balance is not enough.", status_code=400)
        except AgentPolicyDenied:
            return mcp_error_response(-32003, "API key policy does not allow this agent.", status_code=403)
        except exceptions.PermissionDenied:
            return mcp_error_response(-32003, "Agent use permission is required.", status_code=403)
        except AgentRuntimeError as exc:
            return mcp_error_response(-32000, str(exc.detail), status_code=exc.status_code)
        response = HttpResponse(result.body, status=result.status_code)
        for key, value in safe_mcp_response_headers(result.headers).items():
            response[key] = value
        return response

    def _stream_proxy(self, request, agent_id, method: str, headers: dict[str, str]):
        try:
            result = call_runtime_mcp_stream(
                request=request,
                agent_id=agent_id,
                method=method,
                body=request.body,
                headers=headers,
                upstream_path=self.upstream_path,
            )
        except BalanceNotEnough:
            return mcp_error_response(-32001, "Wallet balance is not enough.", status_code=400)
        except AgentPolicyDenied:
            return mcp_error_response(-32003, "API key policy does not allow this agent.", status_code=403)
        except AgentStreamingNotSupported:
            return mcp_error_response(-32004, "Agent runtime streaming is not supported.", status_code=400)
        except exceptions.PermissionDenied:
            return mcp_error_response(-32003, "Agent use permission is required.", status_code=403)
        except AgentRuntimeError as exc:
            return mcp_error_response(-32000, str(exc.detail), status_code=exc.status_code)
        response = StreamingHttpResponse(result.chunks, status=result.status_code, content_type=result.headers.get("Content-Type", "text/event-stream"))
        for key, value in safe_mcp_response_headers(result.headers).items():
            if key.lower() not in {"content-type", "content-length"}:
                response[key] = value
        response["Cache-Control"] = result.headers.get("Cache-Control", "no-cache")
        response["X-Accel-Buffering"] = "no"
        return response

class AgentLegacySSEProxyView(AgentMCPProxyView):
    upstream_path = "/sse"

class AgentLegacySSEMessagesProxyView(AgentMCPProxyView):
    upstream_path = "/messages/"

def mcp_error_response(code: int, message: str, request_id=None, status_code: int = 400):
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "error": {"code": code, "message": message},
            "id": request_id,
        },
        separators=(",", ":"),
    )
    return HttpResponse(body.encode("utf-8"), status=status_code, content_type="application/json")

def wants_event_stream(headers: dict[str, str]) -> bool:
    return "text/event-stream" in str(headers.get("ACCEPT") or headers.get("Accept") or "").lower()
