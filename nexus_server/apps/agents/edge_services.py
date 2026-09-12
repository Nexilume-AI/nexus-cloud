from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
from datetime import timedelta
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import exceptions
from rest_framework import status

from apps.audit.services import log_audit
from apps.common.models import SoftDeleteModel
from apps.common.subjects import request_subject
from apps.common.resource_catalog import discoverable_resource_queryset, resolve_ownership_project
from apps.common.authorization import has_nexus_permission
from apps.common.request_context import get_tenant_from_request

from .models import (
    Agent,
    AgentDeployment,
    AgentRuntimeDeployment,
    EdgeAgentRegistration,
    EdgeNode,
    EdgePairingCode,
)
from .edge_policy import edge_policy
from .edge_certificates import issue_device_certificate, issue_ingress_certificate
from .mobile_access import apply_mobile_policy, sdk_mobile_declaration
from .runtime_services import public_mcp_url
from .services import get_agent


def secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


EDGE_ROUTER_ACTIONS = {
    "create_own": "workspace.edge_router.create_own",
    "read_own": "workspace.edge_router.read_own",
    "update_own": "workspace.edge_router.update_own",
    "revoke_own": "workspace.edge_router.revoke_own",
    "use_own": "workspace.edge_router.use_own",
    "audit": "workspace.edge_router.audit",
    "revoke_any": "workspace.edge_router.revoke_any",
}

EDGE_PRESENCE_LEASE_SECONDS = 300


from .edge_presence import (
    _presence_sweeper_status_file, _presence_sweeper_interval_seconds,
    record_presence_sweeper_heartbeat, presence_sweeper_status,
    _fail_edge_node_runtimes, expire_edge_node_presence,
)


class EdgeOwnerUnavailable(exceptions.APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The enrolled router has no human owner for managed Agent provisioning."
    default_code = "OWNER_UNAVAILABLE"


def _managed_agent_owner(node: EdgeNode):
    return edge_policy().managed_agent_owner(node)


def edge_router_capabilities(*, request) -> dict[str, bool]:
    tenant = get_tenant_from_request(request)
    return {
        name: has_nexus_permission(request.user, tenant, action)
        for name, action in EDGE_ROUTER_ACTIONS.items()
    }


def require_edge_router_permission(*, request, tenant, action: str, node: EdgeNode | None = None) -> None:
    permission = EDGE_ROUTER_ACTIONS[action]
    if not has_nexus_permission(
        request.user,
        tenant,
        permission,
        resource_type="edge_node" if node else None,
        resource_id=str(node.id) if node else None,
    ):
        raise exceptions.PermissionDenied("OpenWrt Router permission is required.")


def _request_user_or_none(request):
    user = getattr(request, "user", None)
    return user if getattr(user, "is_authenticated", False) and not getattr(user, "is_service_account_principal", False) else None


def _resolve_router_project(*, tenant, project_id: str | None):
    return edge_policy().resolve_router_project(tenant=tenant, project_id=project_id)


def create_pairing_code(
    *, request, expires_in_seconds: int = 600, project_id: str | None = None,
    ownership: dict | None = None,
) -> tuple[EdgePairingCode, str]:
    tenant = get_tenant_from_request(request)
    require_edge_router_permission(request=request, tenant=tenant, action="create_own")
    if ownership is not None:
        project, _ownership_inferred = resolve_ownership_project(
            request=request, tenant=tenant, ownership=ownership,
        )
    else:
        project = _resolve_router_project(tenant=tenant, project_id=str(project_id) if project_id else None)
    subject = request_subject(request)
    plaintext = f"pair_{secrets.token_urlsafe(32)}"
    pairing = EdgePairingCode.objects.create(
        tenant=tenant,
        project=project,
        owner_subject_type=subject.principal_type,
        owner_subject_hash=subject.subject_hash,
        token_hash=secret_hash(plaintext),
        expires_at=timezone.now() + timedelta(seconds=expires_in_seconds),
        created_by=_request_user_or_none(request),
    )
    log_audit(
        request=request,
        action="agents.edge.pairing.create",
        actor=request.user,
        resource_type="edge_pairing_code",
        resource_id=pairing.id,
        metadata={"expires_at": pairing.expires_at.isoformat(), "project_id": str(pairing.project_id or "")},
    )
    return pairing, plaintext


@transaction.atomic
def enroll_edge_node(*, data: dict[str, Any]) -> tuple[EdgeNode, str, dict[str, str] | None]:
    now = timezone.now()
    pairing = (
        # Lock the pairing code, not its nullable project/creator outer joins.
        EdgePairingCode.objects.select_for_update(of=("self",))
        .select_related("tenant", "project", "created_by")
        .filter(token_hash=secret_hash(data["pairing_code"]), used_at__isnull=True, expires_at__gt=now)
        .first()
    )
    if pairing is None:
        raise exceptions.AuthenticationFailed("Pairing code is invalid, expired, or already used.")
    edge_policy().validate_pairing(pairing)
    existing = EdgeNode.objects.select_for_update().filter(
        tenant=pairing.tenant, router_id=data["router_id"]
    ).first()
    if existing is not None and existing.status != SoftDeleteModel.STATUS_DELETED:
        same_owner = (
            bool(existing.owner_subject_hash)
            and existing.owner_subject_type == pairing.owner_subject_type
            and hmac.compare_digest(existing.owner_subject_hash, pairing.owner_subject_hash)
        )
        if not same_owner:
            raise exceptions.ValidationError(
                "This router ID is already enrolled by another identity in the tenant."
            )
    managed_certificate = None
    if data.get("device_csr"):
        managed_certificate = issue_device_certificate(
            csr_pem=data["device_csr"], router_id=data["router_id"], domain_id=data["domain_id"].lower()
        )
        certificate_thumbprint = managed_certificate["sha256"]
    else:
        certificate_thumbprint = str(data.get("device_cert_thumbprint") or "").lower()
    if getattr(settings, "NEXUS_EDGE_REQUIRE_MTLS_HEADER", False) and not certificate_thumbprint:
        raise exceptions.ValidationError("A device CSR or OpenWrt device certificate thumbprint is required.")
    device_token = f"edge_{secrets.token_urlsafe(40)}"
    capabilities = dict(data.get("capabilities") or {})
    previous_generation = 0
    if existing is not None and isinstance(existing.capabilities, dict):
        try:
            previous_generation = max(
                0, int(existing.capabilities.get("_nexus_enrollment_generation") or 0)
            )
        except (TypeError, ValueError):
            previous_generation = 0
    # The device cannot choose this value. It is a safe, non-secret signal
    # that distinguishes an in-place identity rotation from a heartbeat.
    capabilities["_nexus_enrollment_generation"] = previous_generation + 1
    node_values = {
        "project": pairing.project,
        "owner_subject_type": pairing.owner_subject_type,
        "owner_subject_hash": pairing.owner_subject_hash,
        "registered_by": pairing.created_by,
        "domain_id": data["domain_id"].lower(),
        "display_name": data.get("display_name") or data["router_id"],
        "device_token_hash": secret_hash(device_token),
        "device_cert_thumbprint": certificate_thumbprint,
        "pending_device_cert_thumbprint": "",
        "pending_device_cert_expires_at": None,
        "connection_status": EdgeNode.CONNECTION_PENDING,
        "presence_protocol_version": EdgeNode.PRESENCE_PROTOCOL_LEGACY,
        "last_presence_at": None,
        "presence_expires_at": None,
        "connection_status_reason": EdgeNode.PRESENCE_REASON_PENDING,
        "ipv6_mode": data.get("ipv6_mode") or EdgeNode.IPV6_ROUTED_PREFIX,
        "connectivity_mode": data.get("connectivity_mode") or EdgeNode.CONNECTIVITY_DIRECT_IPV6,
        "relay_id": "",
        "relay_assignment_id": "",
        "relay_lease_expires_at": None,
        "software_version": data.get("software_version", ""),
        "capabilities": capabilities,
        "last_seen_at": None,
        "status": SoftDeleteModel.STATUS_ACTIVE,
        "deleted_at": None,
    }
    if existing is None:
        node = EdgeNode.objects.create(tenant=pairing.tenant, router_id=data["router_id"], **node_values)
    else:
        node = existing
        stale_registrations = EdgeAgentRegistration.objects.filter(node=node).exclude(
            status=SoftDeleteModel.STATUS_DELETED
        )
        stale_registration_ids = list(stale_registrations.values_list("id", flat=True))
        AgentRuntimeDeployment.objects.filter(edge_registration_id__in=stale_registration_ids).update(
            status=AgentRuntimeDeployment.STATUS_FAILED,
            health_status=AgentRuntimeDeployment.HEALTH_UNHEALTHY,
            last_error="OpenWrt Router identity was re-enrolled.",
        )
        stale_registrations.filter(
            binding_mode=EdgeAgentRegistration.BINDING_MANUAL
        ).update(agent=None)
        stale_registrations.update(
            status=SoftDeleteModel.STATUS_DELETED,
            deleted_at=now,
            health_status=EdgeAgentRegistration.HEALTH_UNHEALTHY,
        )
        for field, value in node_values.items():
            setattr(node, field, value)
        node.save(update_fields=[*node_values.keys(), "updated_at"])
    pairing.used_at = now
    pairing.save(update_fields=["used_at"])
    return node, device_token, managed_certificate


def authenticate_edge_node(request) -> EdgeNode:
    authorization = str(request.headers.get("Authorization") or "")
    if authorization.startswith("Edge "):
        plaintext = authorization[5:].strip()
    elif authorization.startswith("Bearer "):
        plaintext = authorization[7:].strip()
    else:
        plaintext = ""
    if not plaintext:
        raise exceptions.AuthenticationFailed("Edge device credential is required.")

    token_hash = secret_hash(plaintext)
    node = EdgeNode.objects.filter(
        device_token_hash=token_hash,
        status=SoftDeleteModel.STATUS_ACTIVE,
    ).first()
    if node is None or not hmac.compare_digest(node.device_token_hash, token_hash):
        raise exceptions.AuthenticationFailed("Edge device credential is invalid.")

    edge_policy().validate_node(node)
    presented_thumbprint = str(request.headers.get("X-Nexus-Client-Cert-SHA256") or "").lower()
    require_mtls = bool(getattr(settings, "NEXUS_EDGE_REQUIRE_MTLS_HEADER", False))
    if require_mtls and not presented_thumbprint:
        raise exceptions.AuthenticationFailed("A verified mTLS client certificate is required.")
    if node.device_cert_thumbprint and presented_thumbprint != node.device_cert_thumbprint:
        pending_matches = (
            presented_thumbprint
            and presented_thumbprint == node.pending_device_cert_thumbprint
            and node.pending_device_cert_expires_at
            and node.pending_device_cert_expires_at > timezone.now()
        )
        if not pending_matches:
            raise exceptions.AuthenticationFailed("Edge device certificate does not match the enrolled identity.")
        node.device_cert_thumbprint = presented_thumbprint
        node.pending_device_cert_thumbprint = ""
        node.pending_device_cert_expires_at = None
        node.save(
            update_fields=[
                "device_cert_thumbprint",
                "pending_device_cert_thumbprint",
                "pending_device_cert_expires_at",
                "updated_at",
            ]
        )
    return node


@transaction.atomic
def renew_edge_device_certificate(*, request, csr_pem: str) -> dict[str, str]:
    node = authenticate_edge_node(request)
    identity = issue_device_certificate(csr_pem=csr_pem, router_id=node.router_id, domain_id=node.domain_id)
    node.pending_device_cert_thumbprint = identity["sha256"]
    node.pending_device_cert_expires_at = timezone.now() + timedelta(days=1)
    node.save(update_fields=["pending_device_cert_thumbprint", "pending_device_cert_expires_at", "updated_at"])
    return identity


def renew_edge_ingress_certificate(*, request, csr_pem: str) -> dict[str, str]:
    node = authenticate_edge_node(request)
    return issue_ingress_certificate(csr_pem=csr_pem, node_id=str(node.id))


@transaction.atomic
def renew_edge_presence(*, request, data: dict[str, Any]) -> tuple[EdgeNode, int]:
    authenticated = authenticate_edge_node(request)
    node = EdgeNode.objects.select_for_update().filter(
        id=authenticated.id,
        status=SoftDeleteModel.STATUS_ACTIVE,
    ).first()
    if node is None:
        raise exceptions.NotFound("OpenWrt Router not found.")

    now = timezone.now()
    state = data.get("state") or EdgeNode.CONNECTION_ONLINE
    node.presence_protocol_version = EdgeNode.PRESENCE_PROTOCOL_V1
    node.last_presence_at = now
    node.presence_expires_at = now + timedelta(seconds=EDGE_PRESENCE_LEASE_SECONDS)
    node.connection_status = state
    if state == EdgeNode.CONNECTION_ONLINE:
        # Only a complete successful sync clears the last failure. A successful
        # registration alone could belong to another Agent in a partial sync.
        node.registration_diagnostic = {}
    node.connection_status_reason = (
        EdgeNode.PRESENCE_REASON_DEGRADED
        if state == EdgeNode.CONNECTION_DEGRADED
        else EdgeNode.PRESENCE_REASON_CONNECTED
    )
    node.connectivity_mode = data["connectivity_mode"]
    node.last_seen_at = now
    capabilities = dict(node.capabilities) if isinstance(node.capabilities, dict) else {}
    capabilities["router_presence_v1"] = True
    capabilities.update({
        str(name): bool(enabled)
        for name, enabled in dict(data.get("capabilities") or {}).items()
    })
    node.capabilities = capabilities
    node.save(
        update_fields=[
            "presence_protocol_version",
            "last_presence_at",
            "presence_expires_at",
            "connection_status",
            "connection_status_reason",
            "registration_diagnostic",
            "connectivity_mode",
            "last_seen_at",
            "capabilities",
            "updated_at",
        ]
    )

    AgentRuntimeDeployment.objects.filter(
        edge_registration__node=node,
        edge_registration__status=SoftDeleteModel.STATUS_ACTIVE,
        edge_registration__lease_expires_at__gt=now,
        edge_registration__agent__isnull=False,
        status=AgentRuntimeDeployment.STATUS_FAILED,
        last_error__in=[
            "OpenWrt Router presence expired.",
            "OpenWrt Router stopped its cloud presence.",
        ],
    ).update(
        status=AgentRuntimeDeployment.STATUS_ACTIVE,
        health_status=AgentRuntimeDeployment.HEALTH_UNKNOWN,
        last_error="",
    )
    return node, EDGE_PRESENCE_LEASE_SECONDS


@transaction.atomic
def stop_edge_presence(*, request) -> EdgeNode:
    authenticated = authenticate_edge_node(request)
    node = EdgeNode.objects.select_for_update().filter(
        id=authenticated.id,
        status=SoftDeleteModel.STATUS_ACTIVE,
    ).first()
    if node is None:
        raise exceptions.NotFound("OpenWrt Router not found.")
    now = timezone.now()
    node.presence_protocol_version = EdgeNode.PRESENCE_PROTOCOL_V1
    node.presence_expires_at = now
    node.connection_status = EdgeNode.CONNECTION_OFFLINE
    node.connection_status_reason = EdgeNode.PRESENCE_REASON_STOPPED
    node.save(
        update_fields=[
            "presence_protocol_version",
            "presence_expires_at",
            "connection_status",
            "connection_status_reason",
            "updated_at",
        ]
    )
    _fail_edge_node_runtimes(node=node, reason="OpenWrt Router stopped its cloud presence.")
    return node


def validate_public_ipv6(value: str) -> str:
    try:
        address = ipaddress.IPv6Address(value)
    except ipaddress.AddressValueError as exc:
        raise exceptions.ValidationError({"ipv6_address": ["A valid IPv6 address is required."]}) from exc
    if not address.is_global or address.is_multicast or address.is_link_local or address.is_loopback:
        raise exceptions.ValidationError({"ipv6_address": ["A globally routable unicast IPv6 address is required."]})
    return address.compressed


def _managed_runtime_kind(registration: EdgeAgentRegistration) -> str:
    return (
        AgentRuntimeDeployment.RUNTIME_OPENWRT_RELAY
        if registration.transport == EdgeAgentRegistration.TRANSPORT_RELAY
        else AgentRuntimeDeployment.RUNTIME_OPENWRT_IPV6
    )


def _provision_managed_registration(
    *, request, registration: EdgeAgentRegistration, provisioning: dict[str, Any]
) -> EdgeAgentRegistration:
    """Create or resume the stable private Cloud Agent for one node/origin."""

    agent_name = str(provisioning["agent_name"]).strip()
    manifest_digest = str(provisioning.get("manifest_digest") or "")
    # Deleting the Cloud Agent must not permanently orphan a Router-managed
    # registration. The next renewal creates a fresh private Agent.
    linked_agent = registration.agent
    if linked_agent is not None and linked_agent.status == SoftDeleteModel.STATUS_DELETED:
        registration.agent = None
        registration.binding_mode = EdgeAgentRegistration.BINDING_MANAGED
        registration.save(update_fields=["agent", "binding_mode", "updated_at"])

    if registration.binding_mode == EdgeAgentRegistration.BINDING_SUPPRESSED:
        registration.managed_agent_name = agent_name
        registration.manifest_digest = manifest_digest
        registration.save(update_fields=[
            "managed_agent_name", "manifest_digest", "updated_at"
        ])
        return registration

    # A human-created binding is authoritative. Managed renewals may keep its
    # route alive but never change ownership or replace the selected Agent.
    if (
        registration.binding_mode == EdgeAgentRegistration.BINDING_MANUAL
        and registration.agent_id
    ):
        return registration

    node = registration.node
    owner = _managed_agent_owner(node)

    agent = registration.agent
    computer_declared = bool(registration.computer_requirement)
    computer_requirement = (
        registration.computer_requirement
        if computer_declared
        else Agent.COMPUTER_DISABLED
    )
    workspace_capabilities = (
        list(registration.workspace_capabilities or [])
        if computer_declared
        else []
    )
    mobile_declared = bool(registration.mobile_requirement)
    mobile_requirement = (
        registration.mobile_requirement
        if mobile_declared
        else Agent.MOBILE_DISABLED
    )
    mobile_capabilities = (
        list(registration.mobile_capabilities or [])
        if mobile_declared
        else []
    )
    if agent is None:
        from apps.common.resource_limits import enforce_capability

        enforce_capability(tenant=node.tenant, code="agents.agents")
        agent = Agent.objects.create(
            tenant=node.tenant,
            project=node.project,
            team=node.project.team if node.project_id else None,
            name=agent_name,
            status=Agent.STATUS_ACTIVE,
            visibility=Agent.VISIBILITY_PRIVATE,
            publication_status=Agent.PUBLICATION_UNPUBLISHED,
            computer_requirement=computer_requirement,
            workspace_capabilities=workspace_capabilities,
            mobile_requirement=mobile_requirement,
            mobile_capabilities=mobile_capabilities,
            mobile_policy_source=Agent.MOBILE_POLICY_SDK,
            created_by=owner,
        )
    else:
        agent = Agent.objects.select_for_update().get(id=agent.id)
        update_fields: list[str] = []
        if computer_declared and (
            agent.computer_requirement != computer_requirement
            or list(agent.workspace_capabilities or []) != workspace_capabilities
        ):
            agent.computer_requirement = computer_requirement
            agent.workspace_capabilities = workspace_capabilities
            update_fields.extend(["computer_requirement", "workspace_capabilities"])
        if update_fields:
            agent.save(update_fields=[*update_fields, "updated_at"])
        if agent.mobile_policy_source == Agent.MOBILE_POLICY_SDK:
            sdk_requirement, sdk_capabilities = sdk_mobile_declaration(registration)
            apply_mobile_policy(
                agent=agent,
                requirement=sdk_requirement,
                capabilities=sdk_capabilities,
                source=Agent.MOBILE_POLICY_SDK,
            )

    current_version = agent.versions.filter(version=agent.current_version).first()
    if current_version is not None:
        tool_runtime_policy = {
            str(tool.get("name")): {
                "task": bool(tool.get("task")),
                "continuable": bool(tool.get("continuable")),
                "recovery_protocol": int(tool.get("recovery_protocol") or 0),
                "demo": bool(tool.get("demo")),
                "chat": bool(tool.get("chat")),
                "interactive": bool(tool.get("interactive")),
                "mobile_scopes": list(tool.get("mobile_scopes") or []),
            }
            for tool in list(registration.mcp_tools or [])
            if isinstance(tool, dict) and tool.get("name")
        }
        current_version.tool_runtime_policy = tool_runtime_policy
        current_version.save(update_fields=[
            "tool_runtime_policy",
            "updated_at",
        ])
    registration.agent = agent
    registration.binding_mode = EdgeAgentRegistration.BINDING_MANAGED
    registration.managed_agent_name = agent_name
    registration.manifest_digest = manifest_digest
    registration.save(update_fields=[
        "agent",
        "binding_mode",
        "managed_agent_name",
        "manifest_digest",
        "updated_at",
    ])

    callable_now = (
        registration.status == SoftDeleteModel.STATUS_ACTIVE
        and registration.lease_expires_at > timezone.now()
        and (
            not node.presence_supported
            or node.effective_connection_status()
            in {EdgeNode.CONNECTION_ONLINE, EdgeNode.CONNECTION_DEGRADED}
        )
    )
    if callable_now:
        registration.health_status = EdgeAgentRegistration.HEALTH_HEALTHY
        registration.save(update_fields=["health_status", "updated_at"])
    deployment, _ = AgentDeployment.objects.update_or_create(
        agent=agent,
        env="prod",
        defaults={
            "version": agent.versions.filter(version=agent.current_version).first(),
            "status": (
                AgentDeployment.STATUS_ACTIVE
                if callable_now else AgentDeployment.STATUS_DEPLOYING
            ),
            "endpoint_url": public_mcp_url(request=request, agent=agent),
            "deployed_by": owner,
        },
    )
    AgentRuntimeDeployment.objects.update_or_create(
        edge_registration=registration,
        defaults={
            "tenant": node.tenant,
            "agent": agent,
            "env": "prod",
            "project": node.project,
            "agent_deployment": deployment,
            "runtime_kind": _managed_runtime_kind(registration),
            "image": None,
            "status": (
                AgentRuntimeDeployment.STATUS_ACTIVE
                if callable_now else AgentRuntimeDeployment.STATUS_DEPLOYING
            ),
            "container_id": "",
            "internal_mcp_url": registration.endpoint_url,
            "health_status": (
                AgentRuntimeDeployment.HEALTH_HEALTHY
                if callable_now else AgentRuntimeDeployment.HEALTH_UNKNOWN
            ),
            "last_error": "" if callable_now else "Waiting for OpenWrt Router presence.",
            "workspace_connection": None,
            "workspace_root": "",
            "workspace_token": "",
            "deployed_by": owner,
        },
    )
    return registration


@transaction.atomic
def resume_edge_managed_binding(
    *, request, registration_id: str
) -> EdgeAgentRegistration:
    tenant = get_tenant_from_request(request)
    require_edge_router_permission(
        request=request, tenant=tenant, action="use_own"
    )
    subject = request_subject(request)
    registration = (
        EdgeAgentRegistration.objects.select_for_update(of=("self",))
        .select_related("node__tenant", "node__project__team", "node__registered_by", "agent")
        .filter(
            id=registration_id,
            node__tenant=tenant,
            node__owner_subject_hash=subject.subject_hash,
        )
        .first()
    )
    if registration is None:
        raise exceptions.NotFound("OpenWrt Agent registration not found.")
    if registration.binding_mode != EdgeAgentRegistration.BINDING_SUPPRESSED:
        raise exceptions.ValidationError(
            "Only a suppressed managed binding can be resumed."
        )
    if registration.agent_id is None:
        raise exceptions.ValidationError(
            "Suppressed registration no longer references a managed Agent."
        )
    registration.binding_mode = EdgeAgentRegistration.BINDING_MANAGED
    registration.save(update_fields=["binding_mode", "updated_at"])
    return _provision_managed_registration(
        request=request,
        registration=registration,
        provisioning={
            "agent_name": registration.managed_agent_name or registration.agent.name,
            "manifest_digest": registration.manifest_digest,
        },
    )


def upsert_edge_registration(*, request, data: dict[str, Any]) -> EdgeAgentRegistration:
    authenticated_node = authenticate_edge_node(request)
    attempted_at = timezone.now()
    try:
        return _upsert_edge_registration(request=request, data=data, authenticated_node=authenticated_node)
    except exceptions.APIException as error:
        # Record after the registration transaction rolls back. Never persist
        # exception text, submitted manifests, endpoints, or device credentials.
        diagnostic = edge_policy().registration_failure(error=error, attempted_at=attempted_at)
        with transaction.atomic():
            node = EdgeNode.objects.select_for_update().filter(
                pk=authenticated_node.pk, status=SoftDeleteModel.STATUS_ACTIVE,
            ).first()
            # An older failed attempt must not overwrite a newer successful
            # Presence cycle, nor revive a revoked/expired Router.
            newer_diagnostic = node and str((node.registration_diagnostic or {}).get("occurred_at", "")) > attempted_at.isoformat()
            if node and not newer_diagnostic and not (node.last_presence_at and node.last_presence_at > attempted_at):
                node.registration_diagnostic = diagnostic
                fields = ["registration_diagnostic", "updated_at"]
                if node.effective_connection_status() in {EdgeNode.CONNECTION_ONLINE, EdgeNode.CONNECTION_DEGRADED}:
                    node.connection_status = EdgeNode.CONNECTION_DEGRADED
                    node.connection_status_reason = EdgeNode.PRESENCE_REASON_DEGRADED
                    fields += ["connection_status", "connection_status_reason"]
                node.save(update_fields=fields)
        raise


@transaction.atomic
def _upsert_edge_registration(*, request, data: dict[str, Any], authenticated_node: EdgeNode) -> EdgeAgentRegistration:
    node = (
        # Serialize this Router's renewals while only reading related identity
        # data. PostgreSQL cannot lock the nullable side of these outer joins.
        EdgeNode.objects.select_for_update(of=("self",))
        .select_related("project__team", "registered_by", "tenant")
        .get(pk=authenticated_node.pk)
    )
    now = timezone.now()
    transport = data.get("transport") or EdgeAgentRegistration.TRANSPORT_DIRECT_IPV6
    address = validate_public_ipv6(data["ipv6_address"]) if transport == EdgeAgentRegistration.TRANSPORT_DIRECT_IPV6 else None
    if transport == EdgeAgentRegistration.TRANSPORT_RELAY:
        from .relay_services import relay_endpoints

        relay_id = str(data.get("relay_id") or "")
        relay = relay_endpoints().get(relay_id)
        if not isinstance(relay, dict):
            raise exceptions.ValidationError({"relay_id": ["Relay is not trusted by Nexus Server."]})
        if data.get("relay_router_id") != node.router_id:
            raise exceptions.ValidationError({"relay_router_id": ["Relay target must match the enrolled router."]})
        if data.get("relay_assignment_id") != node.relay_assignment_id or relay_id != node.relay_id:
            raise exceptions.ValidationError({"relay_assignment_id": ["Relay assignment is not current for this router."]})
        if not node.relay_lease_expires_at or node.relay_lease_expires_at <= now:
            raise exceptions.ValidationError("Relay assignment lease has expired.")
    existing = EdgeAgentRegistration.objects.select_for_update().filter(node=node, origin=data["origin"]).first()
    generation = int(data["generation"])
    if existing is not None and generation < existing.generation:
        raise exceptions.ValidationError("Registration generation cannot move backwards.")
    protocols = list(dict.fromkeys(data["protocols"]))
    capabilities = list(dict.fromkeys(str(value) for value in data["capabilities"]))
    defaults = {
        "route_id": data["route_id"],
        "protocols": protocols,
        "capabilities": capabilities,
        "mcp_tools": data.get("mcp_tools") or [],
        "transport": transport,
        "ipv6_address": address,
        "port": int(data["port"]) if data.get("port") else None,
        "path": data.get("path") or "",
        "scheme": "https" if transport == EdgeAgentRegistration.TRANSPORT_DIRECT_IPV6 else "",
        "tls_server_name": str(data.get("tls_server_name") or "").lower(),
        "ca_bundle_id": data.get("ca_bundle_id") or "",
        "relay_id": data.get("relay_id") or "",
        "relay_router_id": data.get("relay_router_id") or "",
        "relay_assignment_id": data.get("relay_assignment_id") or "",
        "generation": generation,
        "lease_expires_at": now + timedelta(seconds=int(data["lease_seconds"])),
        "last_renewed_at": now,
        "status": SoftDeleteModel.STATUS_ACTIVE,
        "deleted_at": None,
        "health_status": EdgeAgentRegistration.HEALTH_UNKNOWN,
    }
    if "computer" in data:
        defaults.update({
            "computer_requirement": data["computer"]["requirement"],
            "workspace_capabilities": list(
                data["computer"]["workspace_capabilities"]
            ),
        })
    if "mobile" in data:
        defaults.update({
            "mobile_requirement": data["mobile"]["requirement"],
            "mobile_capabilities": list(data["mobile"]["mobile_capabilities"]),
        })
    else:
        # Downgrading firmware cannot leave an old Caller Mobile declaration active.
        defaults.update({"mobile_requirement": "", "mobile_capabilities": []})
    registration, _ = EdgeAgentRegistration.objects.update_or_create(
        node=node,
        origin=data["origin"],
        defaults=defaults,
    )
    node.connectivity_mode = (
        EdgeNode.CONNECTIVITY_RELAY
        if transport == EdgeAgentRegistration.TRANSPORT_RELAY
        else EdgeNode.CONNECTIVITY_DIRECT_IPV6
    )
    node.save(update_fields=["connectivity_mode", "updated_at"])
    if data.get("provisioning"):
        registration = _provision_managed_registration(
            request=request,
            registration=registration,
            provisioning=data["provisioning"],
        )
    node_is_callable = (
        not node.presence_supported
        or node.effective_connection_status()
        in {EdgeNode.CONNECTION_ONLINE, EdgeNode.CONNECTION_DEGRADED}
    )
    if (
        registration.agent_id
        and registration.binding_mode != EdgeAgentRegistration.BINDING_SUPPRESSED
        and node_is_callable
    ):
        managed_health = (
            AgentRuntimeDeployment.HEALTH_HEALTHY
            if registration.binding_mode == EdgeAgentRegistration.BINDING_MANAGED
            else AgentRuntimeDeployment.HEALTH_UNKNOWN
        )
        AgentRuntimeDeployment.objects.filter(edge_registration=registration).update(
            internal_mcp_url=registration.endpoint_url,
            status=AgentRuntimeDeployment.STATUS_ACTIVE,
            health_status=managed_health,
            last_error="",
        )
    return registration


@transaction.atomic
def unregister_edge_registration(*, request, registration_id: str) -> EdgeAgentRegistration:
    node = authenticate_edge_node(request)
    registration = (
        EdgeAgentRegistration.objects.select_for_update()
        .filter(node=node, id=registration_id)
        .exclude(status=SoftDeleteModel.STATUS_DELETED)
        .first()
    )
    if registration is None:
        raise exceptions.NotFound("OpenWrt Agent registration not found.")
    registration.status = SoftDeleteModel.STATUS_DELETED
    registration.deleted_at = timezone.now()
    registration.health_status = EdgeAgentRegistration.HEALTH_UNHEALTHY
    registration.save(update_fields=["status", "deleted_at", "health_status", "updated_at"])
    AgentRuntimeDeployment.objects.filter(edge_registration=registration).update(
        status=AgentRuntimeDeployment.STATUS_FAILED,
        health_status=AgentRuntimeDeployment.HEALTH_UNHEALTHY,
        last_error="OpenWrt Agent registration was withdrawn.",
    )
    return registration


def list_edge_nodes(*, request, scope: str = "own", project_id: str | None = None):
    tenant = get_tenant_from_request(request)
    queryset = EdgeNode.objects.filter(tenant=tenant).exclude(status=SoftDeleteModel.STATUS_DELETED)
    if scope == "admin":
        require_edge_router_permission(request=request, tenant=tenant, action="audit")
    else:
        require_edge_router_permission(request=request, tenant=tenant, action="read_own")
        queryset = discoverable_resource_queryset(
            queryset, request=request, tenant=tenant, resource_type="edge_node",
            creator_field="registered_by",
        )
    if project_id and str(request.query_params.get("view_scope") or "current") == "current":
        project = _resolve_router_project(tenant=tenant, project_id=project_id)
        queryset = queryset.filter(Q(project=project) | Q(project__isnull=True))
    return queryset.select_related("project", "registered_by").order_by("display_name", "router_id")


def get_edge_node(*, request, node_id: str, scope: str = "own") -> EdgeNode:
    tenant = get_tenant_from_request(request)
    queryset = EdgeNode.objects.filter(tenant=tenant, id=node_id).exclude(status=SoftDeleteModel.STATUS_DELETED)
    if scope == "admin":
        require_edge_router_permission(request=request, tenant=tenant, action="audit")
    else:
        require_edge_router_permission(request=request, tenant=tenant, action="read_own")
        queryset = discoverable_resource_queryset(
            queryset, request=request, tenant=tenant, resource_type="edge_node",
            creator_field="registered_by",
        )
    node = queryset.select_related("project", "registered_by").first()
    if node is None:
        raise exceptions.NotFound("OpenWrt Router not found.")
    return node


@transaction.atomic
def update_edge_node(*, request, node_id: str, data: dict[str, Any]) -> EdgeNode:
    node = get_edge_node(request=request, node_id=node_id)
    require_edge_router_permission(request=request, tenant=node.tenant, action="update_own", node=node)
    # Catalog discovery and a role's own-device action do not authorize editing
    # another publisher's Router. Use the same object authority shown by the UI.
    if not edge_policy().node_context(request=request, resource_type="edge_node", obj=node)["access"]["can_manage"]:
        raise exceptions.PermissionDenied("Router management permission is required.")
    changed: list[str] = []
    if "display_name" in data:
        node.display_name = data["display_name"]
        changed.append("display_name")
    if "project_id" in data:
        project = _resolve_router_project(
            tenant=node.tenant,
            project_id=str(data["project_id"]) if data["project_id"] else None,
        )
        if node.project_id != getattr(project, "id", None):
            active_binding = node.agent_registrations.exclude(status=SoftDeleteModel.STATUS_DELETED).filter(
                Q(agent__isnull=False)
                | Q(runtime_deployment__status__in=[AgentRuntimeDeployment.STATUS_ACTIVE, AgentRuntimeDeployment.STATUS_DEPLOYING])
            ).exists()
            if active_binding:
                raise exceptions.ValidationError({"project_id": ["Disconnect active Agent bindings before moving this Router."]})
            node.project = project
            changed.append("project")
    if changed:
        node.save(update_fields=[*changed, "updated_at"])
    log_audit(
        request=request,
        action="agents.edge.router.update",
        actor=request.user,
        resource_type="edge_node",
        resource_id=node.id,
        metadata={"fields": changed, "project_id": str(node.project_id or "")},
    )
    return node


@transaction.atomic
def revoke_edge_node(*, request, node_id: str, scope: str = "own") -> EdgeNode:
    node = get_edge_node(request=request, node_id=node_id, scope=scope)
    subject = request_subject(request)
    is_owner = bool(node.owner_subject_hash) and node.owner_subject_hash == subject.subject_hash
    if is_owner:
        require_edge_router_permission(request=request, tenant=node.tenant, action="revoke_own", node=node)
    else:
        require_edge_router_permission(request=request, tenant=node.tenant, action="revoke_any", node=node)
    registrations = EdgeAgentRegistration.objects.select_for_update().filter(node=node).exclude(
        status=SoftDeleteModel.STATUS_DELETED
    )
    registration_ids = list(registrations.values_list("id", flat=True))
    AgentRuntimeDeployment.objects.filter(edge_registration_id__in=registration_ids).update(
        status=AgentRuntimeDeployment.STATUS_FAILED,
        health_status=AgentRuntimeDeployment.HEALTH_UNHEALTHY,
        last_error="OpenWrt Router was revoked.",
    )
    registrations.filter(
        binding_mode=EdgeAgentRegistration.BINDING_MANUAL
    ).update(agent=None)
    registrations.update(
        status=SoftDeleteModel.STATUS_DELETED,
        deleted_at=timezone.now(),
        health_status=EdgeAgentRegistration.HEALTH_UNHEALTHY,
    )
    node.device_token_hash = secret_hash(f"revoked_{secrets.token_urlsafe(48)}")
    node.device_cert_thumbprint = ""
    node.pending_device_cert_thumbprint = ""
    node.pending_device_cert_expires_at = None
    node.connection_status = EdgeNode.CONNECTION_REVOKED
    node.connection_status_reason = EdgeNode.PRESENCE_REASON_REVOKED
    node.presence_expires_at = timezone.now()
    node.status = SoftDeleteModel.STATUS_DELETED
    node.deleted_at = timezone.now()
    node.save(
        update_fields=[
            "device_token_hash",
            "device_cert_thumbprint",
            "pending_device_cert_thumbprint",
            "pending_device_cert_expires_at",
            "connection_status",
            "connection_status_reason",
            "presence_expires_at",
            "status",
            "deleted_at",
            "updated_at",
        ]
    )
    log_audit(
        request=request,
        action="agents.edge.router.revoke",
        actor=request.user,
        resource_type="edge_node",
        resource_id=node.id,
        metadata={"router_id": node.router_id, "registration_count": len(registration_ids), "admin_revoke": not is_owner},
    )
    return node


def list_edge_registrations(*, request, available_only: bool = False):
    tenant = get_tenant_from_request(request)
    require_edge_router_permission(request=request, tenant=tenant, action="use_own")
    subject = request_subject(request)
    queryset = (
        EdgeAgentRegistration.objects.filter(node__tenant=tenant, node__owner_subject_hash=subject.subject_hash)
        .exclude(status=SoftDeleteModel.STATUS_DELETED)
        .select_related("node", "agent")
        .order_by("node__display_name", "origin")
    )
    if available_only:
        queryset = queryset.filter(agent__isnull=True, lease_expires_at__gt=timezone.now())
    project_id = str(getattr(request, "project_id", "") or "")
    if project_id:
        queryset = queryset.filter(Q(node__project_id=project_id) | Q(node__project__isnull=True))
    return queryset


@transaction.atomic
def bind_edge_registration(*, request, agent_id: str, registration_id: str, env: str = "prod") -> AgentRuntimeDeployment:
    agent = get_agent(request=request, agent_id=agent_id)
    if not has_nexus_permission(request.user, agent.tenant, "admin", resource_type="agent", resource_id=str(agent.id)):
        raise exceptions.PermissionDenied("Agent administrator permission is required.")
    require_edge_router_permission(request=request, tenant=agent.tenant, action="use_own")
    subject = request_subject(request)
    registration = (
        EdgeAgentRegistration.objects.select_for_update(of=("self",))
        .select_related("node", "agent")
        .filter(
            id=registration_id,
            node__tenant=agent.tenant,
            node__owner_subject_hash=subject.subject_hash,
            node__status=SoftDeleteModel.STATUS_ACTIVE,
            status=SoftDeleteModel.STATUS_ACTIVE,
        )
        .first()
    )
    if registration is None:
        raise exceptions.NotFound("OpenWrt Agent registration not found.")
    if registration.node.project_id and registration.node.project_id != agent.project_id:
        raise exceptions.ValidationError("OpenWrt Router and Agent must belong to the same Project.")
    if registration.lease_expires_at <= timezone.now():
        raise exceptions.ValidationError("OpenWrt Agent registration lease has expired.")
    if registration.agent_id and registration.agent_id != agent.id:
        raise exceptions.ValidationError("OpenWrt Agent registration is already bound to another Agent.")

    existing = AgentRuntimeDeployment.objects.select_for_update().filter(tenant=agent.tenant, agent=agent, env=env).first()
    if existing and existing.status in {AgentRuntimeDeployment.STATUS_ACTIVE, AgentRuntimeDeployment.STATUS_DEPLOYING}:
        valid_runtime_kind = (
            AgentRuntimeDeployment.RUNTIME_OPENWRT_RELAY
            if registration.transport == EdgeAgentRegistration.TRANSPORT_RELAY
            else AgentRuntimeDeployment.RUNTIME_OPENWRT_IPV6
        )
        if existing.runtime_kind != valid_runtime_kind or existing.edge_registration_id != registration.id:
            raise exceptions.ValidationError("Stop or disconnect the current runtime before switching runtime location.")

    deployment, _ = AgentDeployment.objects.update_or_create(
        agent=agent,
        env=env,
        defaults={
            "version": agent.versions.filter(version=agent.current_version).first(),
            "status": AgentDeployment.STATUS_ACTIVE,
            "endpoint_url": public_mcp_url(request=request, agent=agent),
            "deployed_by": request.user,
        },
    )
    runtime, _ = AgentRuntimeDeployment.objects.update_or_create(
        tenant=agent.tenant,
        agent=agent,
        env=env,
        defaults={
            "project": agent.project,
            "agent_deployment": deployment,
            "runtime_kind": (
                AgentRuntimeDeployment.RUNTIME_OPENWRT_RELAY
                if registration.transport == EdgeAgentRegistration.TRANSPORT_RELAY
                else AgentRuntimeDeployment.RUNTIME_OPENWRT_IPV6
            ),
            "image": None,
            "edge_registration": registration,
            "status": AgentRuntimeDeployment.STATUS_ACTIVE,
            "container_id": "",
            "internal_mcp_url": registration.endpoint_url,
            "health_status": AgentRuntimeDeployment.HEALTH_UNKNOWN,
            "last_error": "",
            "workspace_connection": None,
            "workspace_root": "",
            "workspace_token": "",
            "deployed_by": request.user,
        },
    )
    registration.agent = agent
    registration.health_status = EdgeAgentRegistration.HEALTH_UNKNOWN
    registration.save(update_fields=["agent", "health_status", "updated_at"])
    agent.status = Agent.STATUS_ACTIVE
    agent.save(update_fields=["status", "updated_at"])
    log_audit(
        request=request,
        action="agents.edge.bind",
        actor=request.user,
        resource_type="agent_runtime_deployment",
        resource_id=runtime.id,
        metadata={"agent_id": str(agent.id), "registration_id": str(registration.id), "router_id": registration.node.router_id},
    )
    return runtime


@transaction.atomic
def disconnect_edge_runtime(*, request, agent_id: str, env: str = "prod") -> AgentRuntimeDeployment:
    agent = get_agent(request=request, agent_id=agent_id)
    if not has_nexus_permission(request.user, agent.tenant, "admin", resource_type="agent", resource_id=str(agent.id)):
        raise exceptions.PermissionDenied("Agent administrator permission is required.")
    runtimes = AgentRuntimeDeployment.objects.filter(
        tenant=agent.tenant,
        agent=agent,
        env=env,
        runtime_kind__in=[
            AgentRuntimeDeployment.RUNTIME_OPENWRT_IPV6,
            AgentRuntimeDeployment.RUNTIME_OPENWRT_RELAY,
        ],
    )
    candidate = runtimes.values("id", "edge_registration_id").first()
    if candidate is None:
        raise exceptions.NotFound("OpenWrt runtime binding not found.")
    # Lock registration before runtime, matching renewal/binding. Locking the
    # runtime first can deadlock against a renewal holding the registration.
    # Fetch the nullable relation separately instead of FOR UPDATE on a join.
    registration = (
        EdgeAgentRegistration.objects.select_for_update().filter(pk=candidate["edge_registration_id"]).first()
        if candidate["edge_registration_id"] else None
    )
    runtime = runtimes.select_for_update().filter(pk=candidate["id"]).first()
    if runtime is None:
        raise exceptions.NotFound("OpenWrt runtime binding not found.")
    if runtime.edge_registration_id != candidate["edge_registration_id"]:
        raise exceptions.ValidationError("OpenWrt runtime binding changed. Retry the operation.")
    runtime.status = AgentRuntimeDeployment.STATUS_STOPPED
    runtime.health_status = AgentRuntimeDeployment.HEALTH_UNKNOWN
    runtime.save(update_fields=["status", "health_status", "updated_at"])
    if runtime.agent_deployment_id:
        AgentDeployment.objects.filter(id=runtime.agent_deployment_id).update(status=AgentDeployment.STATUS_STOPPED)
    if registration is not None:
        if registration.binding_mode == EdgeAgentRegistration.BINDING_MANAGED:
            registration.binding_mode = EdgeAgentRegistration.BINDING_SUPPRESSED
        else:
            registration.agent = None
        registration.health_status = EdgeAgentRegistration.HEALTH_UNKNOWN
        registration.save(update_fields=[
            "agent", "binding_mode", "health_status", "updated_at"
        ])
    return runtime
