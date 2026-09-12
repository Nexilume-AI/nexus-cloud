from __future__ import annotations

from django.conf import settings
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.renderers import JSONRenderer
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .edge_auth import edge_jwks
from .edge_policy import edge_policy
from .edge_serializers import (
    EdgeAgentBindSerializer,
    EdgeAgentRegistrationSerializer,
    EdgeAgentRegistrationUpsertSerializer,
    EdgeDeviceCertificateRenewSerializer,
    EdgeIngressCertificateRenewSerializer,
    EdgeNodeDetailSerializer,
    EdgeNodeEnrollSerializer,
    EdgeNodeSerializer,
    EdgeNodeUpdateSerializer,
    EdgePresenceSerializer,
    EdgePairingCodeCreateSerializer,
    EdgeRelayAssignmentSerializer,
)
from .edge_services import (
    authenticate_edge_node,
    bind_edge_registration,
    create_pairing_code,
    disconnect_edge_runtime,
    edge_router_capabilities,
    enroll_edge_node,
    get_edge_node,
    list_edge_nodes,
    list_edge_registrations,
    renew_edge_device_certificate,
    renew_edge_ingress_certificate,
    renew_edge_presence,
    resume_edge_managed_binding,
    revoke_edge_node,
    stop_edge_presence,
    unregister_edge_registration,
    update_edge_node,
    upsert_edge_registration,
)
from .runtime_serializers import AgentRuntimeDeploymentSerializer
from .relay_services import (
    RelayNotConfigured,
    assign_relay,
    forwarding_trust_descriptor,
    relay_configuration_available,
    relay_device_trust_descriptor,
    relay_service_status,
    require_relay_configuration,
    set_relay_control_enabled,
)


class RelayAssignmentJSONRenderer(JSONRenderer):
    """Renderer for the versioned OpenWrt Directory assignment contract."""

    media_type = "application/vnd.nexus.relay-assignment+json"
    format = "relay-assignment"


def _relay_directory_descriptor(*, required: bool = False) -> dict:
    available = relay_configuration_available()
    if required and not available:
        raise RelayNotConfigured()
    if not available:
        return {"available": False}
    return {
        "available": True,
        "endpoint": "/api/v1/edge/v1/relay-assignment/",
        "forwarding": forwarding_trust_descriptor(),
        "device_trust": relay_device_trust_descriptor(),
    }


class EdgePairingCodeCreateView(APIView):
    def post(self, request):
        serializer = EdgePairingCodeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        pairing_data = dict(serializer.validated_data)
        if "project_id" not in pairing_data and "ownership" not in pairing_data:
            pairing_data["project_id"] = getattr(request, "project_id", "") or None
        pairing, plaintext = create_pairing_code(request=request, **pairing_data)
        return Response(
            {
                "pairing_code": plaintext,
                "expires_at": pairing.expires_at,
                "project_id": str(pairing.project_id or ""),
            },
            status=status.HTTP_201_CREATED,
        )


class EdgeJWKSView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    renderer_classes = [JSONRenderer]

    def get(self, request):
        return Response(edge_jwks(), headers={"Cache-Control": "public, max-age=300"})


class EdgeNodeEnrollView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = EdgeNodeEnrollSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if serializer.validated_data.get("connectivity_mode") == "relay":
            require_relay_configuration()
        node, device_token, managed_certificate = enroll_edge_node(data=serializer.validated_data)
        payload = EdgeNodeSerializer(node).data
        payload["device_token"] = device_token
        payload["credential_notice"] = "This device credential is shown once. Store it with root-only permissions."
        payload["edge_base_url"] = str(
            getattr(settings, "NEXUS_EDGE_INGRESS_BASE_URL", settings.NEXUS_PUBLIC_BASE_URL)
        ).rstrip("/")
        if managed_certificate is not None:
            payload["device_identity"] = managed_certificate
        payload["edge_auth"] = {
            "issuer": str(getattr(settings, "NEXUS_EDGE_JWT_ISSUER", "https://nexus.local/edge")),
            "audience": f"urn:nexus:router:{node.router_id}",
            "jwks_path": "/api/v1/edge/.well-known/jwks.json",
            "scope": "agent.route agent.invoke",
        }
        payload["relay_directory"] = _relay_directory_descriptor()
        return Response(payload, status=status.HTTP_201_CREATED)


class EdgeRelayConfigurationView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        node = authenticate_edge_node(request)
        return Response({
            "router_id": node.router_id,
            "relay_directory": _relay_directory_descriptor(required=True),
        })


class EdgeRelayAssignmentView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    renderer_classes = [RelayAssignmentJSONRenderer]

    def post(self, request):
        serializer = EdgeRelayAssignmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(assign_relay(request=request, data=serializer.validated_data))


class EdgeNodeListView(APIView):
    def get(self, request):
        scope = "admin" if str(request.query_params.get("scope") or "").lower() == "admin" else "own"
        project_id = str(request.query_params.get("project_id") or "") or None
        capabilities = edge_router_capabilities(request=request)
        return Response(
            EdgeNodeSerializer(
                list_edge_nodes(request=request, scope=scope, project_id=project_id),
                many=True,
                context={"request": request, "scope": scope, "capabilities": capabilities},
            ).data
        )


class EdgeRouterCapabilitiesView(APIView):
    def get(self, request):
        capabilities = edge_router_capabilities(request=request)
        capabilities["relay_available"] = relay_configuration_available()
        return Response(capabilities)


class EdgeRelayServiceView(APIView):
    """Safe Relay listener discovery with superuser-only operator control."""

    def get(self, request):
        return Response(relay_service_status(can_manage=edge_policy().can_manage_relay(request)))

    def post(self, request):
        if not edge_policy().can_manage_relay(request):
            raise exceptions.PermissionDenied("Only a Nexus super administrator can enable or disable Relay.")
        enabled = request.data.get("enabled")
        if not isinstance(enabled, bool):
            raise exceptions.ValidationError({"enabled": ["This field must be true or false."]})
        current = relay_service_status(can_manage=True)
        if enabled and (not current["configured"] or not current["running"]):
            raise exceptions.ValidationError({
                "enabled": ["Relay cannot be enabled until its credentials and both listeners are healthy."]
            })
        set_relay_control_enabled(enabled)
        return Response(relay_service_status(can_manage=True))


class EdgeNodeDetailView(APIView):
    def get(self, request, node_id):
        scope = "admin" if str(request.query_params.get("scope") or "").lower() == "admin" else "own"
        node = get_edge_node(request=request, node_id=str(node_id), scope=scope)
        return Response(
            EdgeNodeDetailSerializer(
                node,
                context={
                    "request": request,
                    "scope": scope,
                    "capabilities": edge_router_capabilities(request=request),
                },
            ).data
        )

    def patch(self, request, node_id):
        serializer = EdgeNodeUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        node = update_edge_node(request=request, node_id=str(node_id), data=serializer.validated_data)
        return Response(
            EdgeNodeDetailSerializer(
                node,
                context={"request": request, "capabilities": edge_router_capabilities(request=request)},
            ).data
        )


class EdgeNodeRevokeView(APIView):
    def post(self, request, node_id):
        scope = "admin" if str(request.query_params.get("scope") or "").lower() == "admin" else "own"
        revoke_edge_node(request=request, node_id=str(node_id), scope=scope)
        return Response(status=status.HTTP_204_NO_CONTENT)


class EdgeDeviceCertificateRenewView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = EdgeDeviceCertificateRenewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        identity = renew_edge_device_certificate(request=request, csr_pem=serializer.validated_data["device_csr"])
        return Response({"device_identity": identity})


class EdgeDeviceCertificateConfirmView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        node = authenticate_edge_node(request)
        return Response({"confirmed": True, "router_id": node.router_id})


class EdgeIngressCertificateRenewView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = EdgeIngressCertificateRenewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        identity = renew_edge_ingress_certificate(
            request=request,
            csr_pem=serializer.validated_data["ingress_csr"],
        )
        return Response({"ingress_identity": identity})


class EdgeDevicePresenceView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = EdgePresenceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        node, lease_seconds = renew_edge_presence(request=request, data=serializer.validated_data)
        return Response(
            {
                "connection_status": node.effective_connection_status(),
                "connection_status_reason": node.effective_connection_status_reason(),
                "presence_expires_at": node.presence_expires_at,
                "lease_seconds": lease_seconds,
                "server_time": timezone.now(),
                "tenant_id": str(node.tenant_id),
            }
        )

    def delete(self, request):
        stop_edge_presence(request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class EdgeAgentRegistrationListView(APIView):
    def get(self, request):
        available_only = str(request.query_params.get("available") or "").lower() in {"1", "true", "yes"}
        registrations = list_edge_registrations(request=request, available_only=available_only)
        return Response(EdgeAgentRegistrationSerializer(registrations, many=True).data)


class EdgeAgentManagedResumeView(APIView):
    def post(self, request, registration_id):
        registration = resume_edge_managed_binding(
            request=request, registration_id=str(registration_id)
        )
        return Response(EdgeAgentRegistrationSerializer(
            registration, context={"request": request}
        ).data)


class EdgeDeviceAgentRegistrationView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = EdgeAgentRegistrationUpsertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        registration = upsert_edge_registration(request=request, data=serializer.validated_data)
        return Response(
            EdgeAgentRegistrationSerializer(
                registration, context={"request": request}
            ).data,
            status=status.HTTP_201_CREATED,
        )


class EdgeDeviceAgentRegistrationDetailView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def delete(self, request, registration_id):
        unregister_edge_registration(request=request, registration_id=str(registration_id))
        return Response(status=status.HTTP_204_NO_CONTENT)


class AgentEdgeBindingView(APIView):
    def post(self, request, agent_id):
        serializer = EdgeAgentBindSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        runtime = bind_edge_registration(
            request=request,
            agent_id=str(agent_id),
            registration_id=str(serializer.validated_data["registration_id"]),
            env=serializer.validated_data["env"],
        )
        return Response(AgentRuntimeDeploymentSerializer(runtime).data, status=status.HTTP_201_CREATED)

    def delete(self, request, agent_id):
        env = str(request.query_params.get("env") or "prod")
        runtime = disconnect_edge_runtime(request=request, agent_id=str(agent_id), env=env)
        return Response(AgentRuntimeDeploymentSerializer(runtime).data)
