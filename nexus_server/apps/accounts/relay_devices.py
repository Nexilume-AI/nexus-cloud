"""Short-lived device-code approval for the OpenWrt Python SDK.

Tokens only work on this module's device endpoints, never normal management or
inference APIs. Browser approval uses the host session authenticator and CSRF.
"""
import hashlib
import secrets
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from rest_framework import exceptions, serializers
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

from apps.audit.services import log_audit
from apps.common.request_context import get_tenant_from_request
from .relay_auth import BrowserApproval, Native
from .models import RelayDevice


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Spec(serializers.Serializer):
    name = serializers.CharField(max_length=180)
    model = serializers.RegexField(r"\A[^\s\x00-\x1f]{1,128}\Z")
    endpoint = serializers.URLField(max_length=1024)
    protocol = serializers.ChoiceField(choices=["system_one", "openai-compatible"])
    transport = serializers.ChoiceField(choices=["outbound"], required=False)
    provider_id = serializers.UUIDField(required=False)

    def validate(self, data):
        if data.get("provider_id"):
            if data.get("transport") != "outbound":
                raise serializers.ValidationError("Existing Provider binding requires outbound transport.")
            data["provider_id"] = str(data["provider_id"])
        return data

    def validate_endpoint(self, value):
        parsed = urlsplit(value)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise serializers.ValidationError("Use an HTTPS base URL without credentials or query parameters.")
        return value.rstrip("/")


class Start(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    models = Spec(many=True, allow_empty=False)

    def validate_models(self, value):
        if len(value) > 32 or len({r["model"] for r in value}) != len(value):
            raise serializers.ValidationError("Approve between 1 and 32 distinct model IDs.")
        return value


class Secret(serializers.Serializer):
    device_code = serializers.CharField(max_length=128, required=False)
    refresh_token = serializers.CharField(max_length=128, required=False)


class StartThrottle(AnonRateThrottle):
    scope = "relay_device_start"
    rate = "12/hour"


class PollThrottle(AnonRateThrottle):
    scope = "relay_device_poll"
    rate = "60/minute"


def validate(serializer, data):
    instance = serializer(data=data)
    instance.is_valid(raise_exception=True)
    return instance.validated_data


def audit(request, row, action):
    log_audit(request=request, actor=row.user, action="accounts.relay." + action,
        resource_type="relay_device", resource_id=str(row.id), metadata={"models": [r["model"] for r in row.manifest]})


def scope_request(request, row):
    # Explicit, narrow delegation. Device input cannot choose owner, org, project,
    # resource IDs or method. Every service rechecks the owner's current IAM.
    if not row.user or not row.user.is_active or not constant_time_compare(row.password_hash, row.user.get_session_auth_hash()):
        raise exceptions.AuthenticationFailed("Device owner changed. Bind again.")
    delegated = SimpleNamespace(user=row.user, tenant_id=row.tenant_id, project_id="",
        headers={}, META=request.META, session={}, query_params={}, auth=None,
        path=request.path, method=request.method, request_id=getattr(request, "request_id", ""))
    tenant = get_tenant_from_request(delegated)
    if str(tenant.pk) != row.tenant_id:
        raise exceptions.PermissionDenied("Device belongs to a different installation context.")
    from apps.providers.services import require_provider_admin
    from apps.deployments.services import require_deployment_admin
    from apps.routers.services import require_router_admin
    require_provider_admin(request=delegated, tenant=tenant)
    require_deployment_admin(request=delegated, tenant=tenant)
    require_router_admin(request=delegated, tenant=tenant)
    return delegated


def active(row):
    now = timezone.now()
    if row is None or row.status != "active" or row.revoked_at or not row.expires_at or row.expires_at <= now:
        raise exceptions.AuthenticationFailed("Device expired or revoked. Bind again.")


def issue(row):
    now = timezone.now()
    access, refresh = "nrelay_a_" + secrets.token_urlsafe(32), "nrelay_r_" + secrets.token_urlsafe(40)
    row.access_hash, row.refresh_hash = digest(access), digest(refresh)
    row.access_expires_at = min(now + timedelta(minutes=10), row.expires_at)
    row.refresh_expires_at = min(now + timedelta(days=7), row.expires_at)
    row.save()
    return {"status": "connected", "device_id": str(row.id), "tenant_id": row.tenant_id,
        "access_token": access, "refresh_token": refresh, "token_type": "Bearer",
        "expires_in": max(0, int((row.access_expires_at - now).total_seconds())),
        "refresh_expires_at": row.refresh_expires_at, "device_expires_at": row.expires_at,
        "models": row.manifest, "registrations": row.registrations}


class DeviceNative(Native):
    def get_authenticate_header(self, request):
        return 'Bearer realm="nexus-relay"'


class RelayStart(DeviceNative):
    throttle_classes = [StartThrottle]

    def post(self, request):
        data = validate(Start, request.data)
        code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(10))
        device_code = secrets.token_urlsafe(40)
        row = RelayDevice.objects.create(name=data["name"], manifest=data["models"],
            user_code_hash=digest(code), device_code_hash=digest(device_code),
            approval_expires_at=timezone.now() + timedelta(minutes=10))
        origin = str(settings.NEXUS_PUBLIC_BASE_URL).rstrip("/")
        uri = origin + "/api/v1/auth/relay/page/"
        return Response({"device_id": str(row.id), "device_code": device_code,
            "user_code": code[:5] + "-" + code[5:], "verification_uri": uri,
            "verification_uri_complete": uri + "?code=" + code,
            "expires_in": 600, "interval": 5}, status=201)


class RelayToken(DeviceNative):
    throttle_classes = [PollThrottle]

    @transaction.atomic
    def post(self, request):
        data = validate(Secret, request.data)
        if bool(data.get("device_code")) == bool(data.get("refresh_token")):
            raise exceptions.ValidationError("Provide one device code or refresh token.")
        refresh = data.get("refresh_token")
        field = "refresh_hash" if refresh else "device_code_hash"
        row = RelayDevice.objects.select_for_update().filter(**{field: digest(refresh or data["device_code"])}).first()
        now = timezone.now()
        if row is None:
            raise exceptions.AuthenticationFailed("Invalid device credential.")
        if refresh:
            active(row)
            if row.refresh_expires_at <= now:
                raise exceptions.AuthenticationFailed("Refresh expired. Bind again.")
            scope_request(request, row)
            result = issue(row)
            audit(request, row, "refresh")
            return Response(result)
        if row.status not in {"pending", "approved"} or row.approval_expires_at <= now:
            return Response({"error": "access_denied" if row.status == "denied" else "expired_token"}, status=400)
        if row.last_poll_at and (now - row.last_poll_at).total_seconds() < 5:
            return Response({"error": "slow_down", "interval": 10}, status=429)
        row.last_poll_at = now
        row.save(update_fields=["last_poll_at"])
        if row.status == "pending":
            return Response({"error": "authorization_pending", "interval": 5}, status=400)
        scope_request(request, row)
        row.status = "active"
        row.expires_at = now + timedelta(days=30)
        result = issue(row)
        audit(request, row, "connected")
        return Response(result)


class Approval(serializers.Serializer):
    code = serializers.CharField(max_length=16)
    approve = serializers.BooleanField(required=False)
    tenant_id = serializers.UUIDField(required=False)


def code_row(code, lock=False):
    code = code.replace("-", "").strip().upper()
    query = RelayDevice.objects.select_for_update() if lock else RelayDevice.objects
    row = query.filter(user_code_hash=digest(code), approval_expires_at__gt=timezone.now()).first()
    if row is None:
        raise exceptions.NotFound("Code expired or not found. Start a new SDK login.")
    return row


class RelayApproval(BrowserApproval):
    throttle_classes = [PollThrottle]

    def get(self, request):
        data = validate(Approval, request.query_params)
        row = code_row(data["code"])
        if row.user_id and row.user_id != request.user.pk:
            raise exceptions.NotFound()
        # Community exposes only the authenticated installation owner context.
        orgs = [get_tenant_from_request(request)]
        return Response({"device_id": str(row.id), "name": row.name, "models": row.manifest,
            "status": row.status, "organizations": [{"id": str(o.id), "name": o.name} for o in orgs]})

    @transaction.atomic
    def post(self, request):
        data = validate(Approval, request.data)
        if "approve" not in data:
            raise exceptions.ValidationError("Explicit confirmation is required.")
        row = code_row(data["code"], lock=True)
        if row.status != "pending":
            raise exceptions.ValidationError("Request already handled. Start a new login.")
        row.user = request.user
        row.password_hash = request.user.get_session_auth_hash()
        if data["approve"]:
            if not data.get("tenant_id"):
                raise exceptions.ValidationError("Choose an Organization.")
            row.tenant_id = str(data["tenant_id"])
            scope_request(request, row)
        row.status = "approved" if data["approve"] else "denied"
        row.save()
        audit(request, row, row.status)
        return Response({"status": row.status})


def device_auth(request, *, lock=True):
    value = request.headers.get("Authorization", "")
    if not value.startswith("Bearer nrelay_a_") or len(value) > 150:
        raise exceptions.AuthenticationFailed("Relay device access credential required.")
    query = RelayDevice.objects.select_for_update() if lock else RelayDevice.objects
    row = query.filter(access_hash=digest(value[7:])).first()
    active(row)
    if row.access_expires_at <= timezone.now():
        raise exceptions.AuthenticationFailed("Device access expired. Refresh first.")
    scope_request(request, row)
    return row


class Registration(serializers.Serializer):
    model = serializers.CharField(max_length=128)
    endpoint_api_key = serializers.RegexField(r"\A[^\s\x00-\x1f]{1,8192}\Z", write_only=True, required=False)


class RelayModels(DeviceNative):
    @transaction.atomic
    def get(self, request):
        row = device_auth(request)
        return Response({"device_id": str(row.id), "models": row.manifest, "registrations": row.registrations})

    @transaction.atomic
    def post(self, request):
        data = validate(Registration, request.data)
        row = device_auth(request)
        spec = next((s for s in row.manifest if s["model"] == data["model"]), None)
        if spec is None:
            raise exceptions.PermissionDenied("Model is outside the browser-approved device scope.")
        if spec.get("transport") == "outbound":
            from .relay_transport import prepare
            result = prepare(scope_request(request, row), row, spec, data.get("endpoint_api_key"))
            audit(request, row, "transport.prepare")
            return Response(result)
        if not data.get("endpoint_api_key"):
            raise exceptions.ValidationError("Endpoint credential required.")
        from .relay_registration import register
        result = register(scope_request(request, row), row, spec, data["endpoint_api_key"])
        audit(request, row, "model.register")
        return Response(result)


def revoke(request, row):
    from .relay_registration import disable_sources
    row.status, row.revoked_at = "revoked", timezone.now()
    row.access_hash, row.refresh_hash = None, None
    row.save()
    row.model_bindings.update(status="revoked")
    disable_sources(row)
    audit(request, row, "revoke")


class RelayRevoke(DeviceNative):
    @transaction.atomic
    def post(self, request):
        row = device_auth(request)
        revoke(request, row)
        return Response({"status": "revoked"})


class RelayDevices(BrowserApproval):
    def get(self, request):
        rows = RelayDevice.objects.filter(user=request.user).exclude(status="pending").order_by("-created_at")[:100]
        return Response({"devices": [{"id": str(r.id), "name": r.name, "status": r.status,
            "expires_at": r.expires_at, "models": r.manifest} for r in rows]})


class RelayBrowserRevoke(BrowserApproval):
    @transaction.atomic
    def post(self, request, device_id):
        row = RelayDevice.objects.select_for_update().filter(id=device_id, user=request.user).first()
        if row is None:
            raise exceptions.NotFound()
        revoke(request, row)
        return Response({"status": "revoked"})
