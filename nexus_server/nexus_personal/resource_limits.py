"""Operator-configured personal Data/Agent capacity, without Plan or wallets.

Collections serialize creation under the existing Tenant row lock. File and
download reservations remain the real DatasetTransfer protocol; this backend
supplies measured limits, Agent counts and exact-byte export usage. Other resource
families fail closed until their personal admission implementation is added.
"""
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import exceptions

from apps.datasets.models import Dataset, DatasetFile, DatasetTransfer
from apps.tenancy.models import Tenant
from .models import PersonalInstallation, PersonalDataExportUsage, PersonalRunReservation, PersonalInvocationUsage
from .services import installation_context, require_personal_distribution


GB = Decimal(1024 ** 3)
CODES = frozenset(("data.collections", "data.files", "data.storage_gb", "data.export_gb_per_30_days", "agents.agents", "agents.mobile_devices", "agents.computers", "agents.concurrent_runs", "agents.run_minutes_per_run", "agents.runs_per_30_days", "agents.run_minutes_per_30_days", "models.provider_connections", "models.model_offers"))
COUNT_CODES = frozenset(("data.collections", "data.files", "agents.agents", "agents.mobile_devices", "agents.computers", "agents.concurrent_runs", "agents.runs_per_30_days", "models.provider_connections", "models.model_offers"))


ROUTER_CODES = frozenset(("models.execution_routers", "models.aggregation_routers"))
HOSTED_MAXIMUM_CODES = frozenset(("agents.hosted_cpu", "agents.hosted_memory_mb"))
CODES = CODES | ROUTER_CODES | HOSTED_MAXIMUM_CODES
COUNT_CODES = COUNT_CODES | ROUTER_CODES


class PersonalCapacityExceeded(exceptions.APIException):
    status_code = 409
    default_code = "PERSONAL_CAPACITY_EXCEEDED"
    default_detail = "Personal instance capacity exceeded. Free capacity or ask the operator to adjust local limits."


class PersonalRunCapacityExceeded(PersonalCapacityExceeded):
    default_code = "PERSONAL_RUN_CAPACITY_EXCEEDED"


def number(value):
    if isinstance(value, (bool, float)) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("Use an exact nonnegative number.")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ValueError("Use an exact nonnegative number.") from None
    if not result.is_finite() or result < 0 or result > 2 ** 63 - 1:
        raise ValueError("Invalid capacity value.")
    return result


class PersonalResourceAdmission:
    def _context(self, tenant):
        _, tenant_id, _ = installation_context()
        if str(getattr(tenant, "pk", tenant)) != tenant_id:
            raise exceptions.NotFound("Personal resource context not found.")
        return tenant_id

    def _limit(self, code):
        setting = "NEXUS_PERSONAL_AGENT_LIMITS" if code.startswith("agents.") else "NEXUS_PERSONAL_DATA_LIMITS"
        if code.startswith("models."):
            setting = "NEXUS_PERSONAL_MODEL_LIMITS"
        limits = getattr(settings, setting, None)
        if code not in CODES or not isinstance(limits, dict) or code not in limits:
            raise ImproperlyConfigured("Personal resource capacity is not configured for this operation.")
        try:
            value = number(limits[code])
            if code in COUNT_CODES and value != value.to_integral_value():
                raise ValueError()
            if code == "agents.run_minutes_per_run" and not Decimal(1) <= value <= Decimal(1440):
                raise ValueError()
        except ValueError:
            raise ImproperlyConfigured("Personal capacity must be an explicit finite nonnegative limit.") from None
        return value

    def capability_state(self, *, tenant, code):
        tenant_id = self._context(tenant)
        limit = self._limit(code)
        reset_at = None
        if code == "agents.concurrent_runs":
            from apps.agents.models import AgentDisplayRun
            runs = AgentDisplayRun.objects.filter(tenant_id=tenant_id,
                run_kind="invocation", status=AgentDisplayRun.STATUS_RUNNING)
            # The committed Run takes over its reserved slot. Even if its
            # release callback is delayed, it must not consume two slots.
            pending = PersonalRunReservation.objects.filter(tenant_id=tenant_id,
                released_at__isnull=True, expires_at__gt=timezone.now()).exclude(
                run_id__in=AgentDisplayRun.objects.filter(tenant_id=tenant_id).values("pk"))
            used = Decimal(runs.count() + pending.count())
        elif code == "agents.run_minutes_per_run" or code in HOSTED_MAXIMUM_CODES:
            # A per-request ceiling consumed by the actual task deadline and
            # MCP timeout / container CPU-memory limits, not an accumulated
            # usage counter. Hosted limits are per-container ceilings.
            used = Decimal(0)
        elif code == "agents.agents":
            from apps.agents.models import Agent
            used = Decimal(Agent.objects.filter(tenant_id=tenant_id).exclude(status="deleted").count())
        elif code == "agents.mobile_devices":
            from apps.mobile.models import MobileDevice
            used = Decimal(MobileDevice.objects.filter(tenant_id=tenant_id).exclude(status="deleted").count())
        elif code == "agents.computers":
            from apps.workspaces.models import WorkspaceConnection
            used = Decimal(WorkspaceConnection.objects.filter(tenant_id=tenant_id, status="active",
                connection_type="runtime").exclude(runtime_device__revoked_at__isnull=False).count())
        elif code == "models.provider_connections":
            from apps.providers.models import ProviderAccount
            used = Decimal(ProviderAccount.objects.filter(tenant_id=tenant_id).exclude(status="deleted")
                .exclude(account_id__startswith="runtime_", runtime_accounts__isnull=False).distinct().count())
        elif code == "models.model_offers":
            from apps.providers.models import ProviderRuntimeModelOffer
            used = Decimal(ProviderRuntimeModelOffer.objects.filter(runtime_account__tenant_id=tenant_id)
                .exclude(status="deleted").exclude(runtime_account__status="deleted").count())
        elif code in ROUTER_CODES:
            from apps.routers.models import Router
            router_type = Router.TYPE_EXECUTION if code == "models.execution_routers" else Router.TYPE_AGGREGATION
            used = Decimal(Router.objects.filter(tenant_id=tenant_id, router_type=router_type).exclude(status="deleted").count())
        elif code == "data.collections":
            used = Decimal(Dataset.objects.filter(tenant_id=tenant_id).exclude(status="deleted").count())
        elif code in {"data.files", "data.storage_gb"}:
            # Retained files still occupy storage after their collection is
            # soft-deleted; don't make capacity vanish before physical cleanup.
            files = DatasetFile.objects.filter(tenant_id=tenant_id).exclude(status="deleted")
            used = Decimal(files.count()) if code == "data.files" else Decimal(
                files.aggregate(total=Sum("size_bytes"))["total"] or 0) / GB
        else:
            anchor = PersonalInstallation.objects.get(slot=1).created_at
            period = timedelta(days=30)
            elapsed = max(0, (timezone.now() - anchor) // period)
            start = anchor + elapsed * period
            reset_at = start + period
            if code.startswith("agents."):
                receipts = PersonalInvocationUsage.objects.filter(tenant_id=tenant_id,
                    started_at__gte=start, started_at__lt=reset_at)
                used = (Decimal(receipts.count()) if code == "agents.runs_per_30_days" else
                    (Decimal(receipts.filter(finished_at__isnull=False).aggregate(total=Sum("latency_ms"))["total"] or 0)
                     + Decimal(receipts.filter(finished_at__isnull=True).aggregate(total=Sum("reserved_ms"))["total"] or 0)) / Decimal(60000))
            else:
                total = PersonalDataExportUsage.objects.filter(
                    transfer__tenant_id=tenant_id, recorded_at__gte=start, recorded_at__lt=reset_at
                ).aggregate(total=Sum("size_bytes"))["total"] or 0
                used = Decimal(total) / GB
        return {"code": code, "used": used, "limit": limit, "remaining": max(Decimal(0), limit - used),
                "reset_at": reset_at, "state": "over_limit" if used > limit else "at_limit" if used == limit else "available"}

    def enforce_capability(self, *, tenant, code, requested=1):
        if code.startswith("models."):
            if not connection.in_atomic_block:
                raise ImproperlyConfigured("Provider admission must run inside the resource creation transaction.")
            tenant_id = self._context(tenant)
            Tenant.objects.select_for_update(no_key=True).get(pk=tenant_id)
        try:
            requested = number(requested)
            if code in COUNT_CODES and requested != requested.to_integral_value():
                raise ValueError()
        except ValueError:
            raise exceptions.ValidationError("Capacity requests must be exact nonnegative amounts.") from None
        state = self.capability_state(tenant=tenant, code=code)
        if state["used"] + requested > state["limit"]:
            error = PersonalRunCapacityExceeded if code == "agents.concurrent_runs" else PersonalCapacityExceeded
            raise error({"code": error.default_code, "resource": code,
                "used": str(state["used"]), "limit": str(state["limit"]),
                "message": str(PersonalCapacityExceeded.default_detail)})
        return state

    def _reservation_identity(self, code, idempotency_key):
        if code != "agents.concurrent_runs":
            raise ImproperlyConfigured("Personal reservations are only implemented for concurrent Runs.")
        try:
            if not isinstance(idempotency_key, str) or not idempotency_key.startswith("agent-run:"):
                raise ValueError()
            run_id = UUID(idempotency_key.removeprefix("agent-run:"))
            if idempotency_key != f"agent-run:{run_id}":
                raise ValueError()
            return run_id
        except ValueError:
            raise exceptions.ValidationError("Invalid Run reservation identity.") from None

    @transaction.atomic
    def reserve_capability(self, *, tenant, code, idempotency_key, amount=1, ttl_seconds=300):
        tenant_id = self._context(tenant)
        run_id = self._reservation_identity(code, idempotency_key)
        self._limit(code)
        try:
            if number(amount) != 1 or type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 300:
                raise ValueError()
        except ValueError:
            raise exceptions.ValidationError("Reserve one Run with a lifetime of 1–300 seconds.") from None
        Tenant.objects.select_for_update(no_key=True).get(pk=tenant_id)
        existing = PersonalRunReservation.objects.filter(pk=run_id).first()
        if existing:
            if str(existing.tenant_id) != tenant_id:
                raise exceptions.NotFound("Run reservation not found.")
            if existing.released_at is not None or existing.expires_at <= timezone.now():
                raise exceptions.ValidationError("Run reservation is no longer active. Start a new request.")
            return existing  # Retries neither consume a slot nor extend its lifetime.
        from apps.agents.models import AgentDisplayRun
        if AgentDisplayRun.objects.filter(pk=run_id).exists():
            raise exceptions.ValidationError("Run already exists; use its continuation workflow.")
        self.enforce_capability(tenant=tenant, code=code)
        return PersonalRunReservation.objects.create(run_id=run_id, tenant_id=tenant_id,
            expires_at=timezone.now() + timedelta(seconds=ttl_seconds))

    @transaction.atomic
    def release_capability_reservation(self, *, tenant, code, idempotency_key):
        require_personal_distribution()
        tenant_id = str(PersonalInstallation.objects.get(slot=1).tenant_id)
        if str(getattr(tenant, "pk", tenant)) != tenant_id:
            raise exceptions.NotFound("Personal resource context not found.")
        run_id = self._reservation_identity(code, idempotency_key)
        Tenant.objects.select_for_update(no_key=True).get(pk=tenant_id)
        # Idempotent cleanup does not depend on the current limit or active
        # owner login: reducing capacity must never prevent slot release.
        PersonalRunReservation.objects.filter(pk=run_id, tenant_id=tenant_id,
            released_at__isnull=True).update(released_at=timezone.now())

    def enforce_resource(self, *, tenant, resource):
        codes = {"datasets": "data.collections", "mobile_device": "agents.mobile_devices", "remote_workspace": "agents.computers"}
        if resource not in codes:
            raise ImproperlyConfigured("Personal admission is not implemented for this resource.")
        return self.enforce_capability(tenant=tenant, code=codes[resource])

    @transaction.atomic
    def record_capability_usage(self, *, tenant, code, amount, idempotency_key, metadata=None):
        tenant_id = self._context(tenant)
        self._limit(code)
        if code != "data.export_gb_per_30_days":
            raise ImproperlyConfigured("Only completed Data exports have personal usage records.")
        if not isinstance(idempotency_key, str) or not idempotency_key.startswith("dataset-export:") or len(idempotency_key) > 128:
            raise exceptions.ValidationError("Invalid export usage identity.")
        if metadata is not None and not isinstance(metadata, dict):
            raise exceptions.ValidationError("Invalid export usage metadata.")
        try:
            amount = number(amount)
        except ValueError:
            raise exceptions.ValidationError("Invalid export amount.") from None
        Tenant.objects.select_for_update(no_key=True).get(pk=tenant_id)
        transfer = DatasetTransfer.objects.select_for_update().filter(
            tenant_id=tenant_id, identity=idempotency_key.removeprefix("dataset-export:"), kind="export"
        ).first()
        if (transfer is None or amount != Decimal(transfer.size_bytes) / GB
                or (metadata or {}).get("dataset_id") != str(transfer.dataset_id)):
            raise exceptions.ValidationError("Export usage does not match its authorized transfer.")
        existing = PersonalDataExportUsage.objects.filter(transfer=transfer).first()
        if existing:
            if existing.idempotency_key != idempotency_key or existing.size_bytes != transfer.size_bytes:
                raise exceptions.ValidationError("Conflicting export usage.")
            return existing
        if transfer.state != "active" or transfer.expires_at <= timezone.now():
            raise exceptions.ValidationError("Export reservation expired or is unavailable.")
        # Shared complete_export commits this row and transfer completion in the
        # same outer transaction. No report can create arbitrary paid usage.
        return PersonalDataExportUsage.objects.create(transfer=transfer,
            idempotency_key=idempotency_key, size_bytes=transfer.size_bytes)
