"""Caller-bound ephemeral WebRTC signaling; media never passes through this API."""
import json
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import APIException

from apps.common.subjects import request_subject
from .models import MobileDevice, MobileVideoSession
from .turn import TurnConfigurationError, ice_configuration

TERMINAL = {"stopped", "failed", "expired"}


def sweep_sessions():
    """Bounded periodic purge even when both endpoints have disappeared."""
    now = timezone.now()
    stale = MobileVideoSession.objects.exclude(state__in=TERMINAL).filter(
        Q(expires_at__lte=now) | Q(viewer_seen_at__lt=now - timedelta(seconds=20)) |
        Q(state="live", device_seen_at__lt=now - timedelta(seconds=10)))
    ids = list(stale.order_by("expires_at").values_list("id", flat=True)[:200])
    return stale.filter(id__in=ids).update(state="expired", signals=[], updated_at=now)


class MobileVideoError(APIException):
    status_code = 409
    default_code = "MOBILE_VIDEO_UNAVAILABLE"
    default_detail = "Live video is unavailable. Start a new screen-sharing session."


def check_session(session):
    now = timezone.now()
    if session.state not in TERMINAL and (
        session.expires_at <= now or session.viewer_seen_at < now - timedelta(seconds=20)
        or session.pairing_hash != session.device.token_hash or session.device.status != "active"
    ):
        session.state = "expired"
        session.signals = []
        session.save(update_fields=["state", "signals", "updated_at"])
    elif session.state == "live" and session.device_seen_at and session.device_seen_at < now - timedelta(seconds=10):
        session.state = "failed"
        session.error_code = "MOBILE_VIDEO_DEVICE_OFFLINE"
        session.signals = []
        session.save(update_fields=["state", "error_code", "signals", "updated_at"])
    return session


def payload(session, *, role, after=0, include_ice=False):
    check_session(session)
    data = {"id": str(session.id), "state": session.state, "error_code": session.error_code,
            "expires_at": session.expires_at.isoformat(), "geometry": session.geometry,
            "geometry_version": session.geometry_version, "sequence": session.sequence,
            "signals": [signal for signal in session.signals if signal["role"] != role and signal["sequence"] > after]}
    if include_ice and session.state not in TERMINAL:
        try:
            data.update(ice_configuration(settings, session.id, lifetime_seconds=900))
        except TurnConfigurationError:
            raise MobileVideoError("Video relay configuration is unavailable.", code="MOBILE_VIDEO_RELAY_UNAVAILABLE") from None
    return data


@transaction.atomic
def start_session(request, device_id):
    from .services import get_mobile_device
    device = get_mobile_device(request=request, device_id=device_id)
    device = MobileDevice.objects.select_for_update().get(pk=device.pk)
    if device.lifecycle_status != "online" or (device.capabilities or {}).get("live_video") != 1:
        raise MobileVideoError("Update Nexus Mobile, enable control and reconnect the phone.")
    for old in device.video_sessions.exclude(state__in=TERMINAL):
        if check_session(old).state not in TERMINAL:
            raise MobileVideoError("Screen sharing is already open. Stop it before starting again.", code="MOBILE_VIDEO_BUSY")
    now = timezone.now()
    session = MobileVideoSession.objects.create(device=device, owner_subject_hash=request_subject(request).subject_hash,
        pairing_hash=device.token_hash, expires_at=now + timedelta(minutes=15), viewer_seen_at=now)
    return payload(session, role="viewer", include_ice=True)


def viewer_session(request, session_id, *, lock=False):
    from .services import get_mobile_device, MobileNotFound
    qs = MobileVideoSession.objects.select_related("device")
    if lock:
        qs = qs.select_for_update(of=("self",))
    session = qs.filter(id=session_id, owner_subject_hash=request_subject(request).subject_hash).first()
    if session is None:
        raise MobileNotFound()
    get_mobile_device(request=request, device_id=str(session.device_id))
    return check_session(session)


def cursor(value):
    if type(value) not in (int, str) or isinstance(value, str) and (not value.isascii() or not value.isdecimal() or len(value) > 10):
        raise MobileVideoError("Invalid signal cursor.", code="MOBILE_VIDEO_SIGNAL_INVALID")
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise MobileVideoError("Invalid signal cursor.", code="MOBILE_VIDEO_SIGNAL_INVALID") from None
    if not 0 <= value <= 2147483647:
        raise MobileVideoError("Invalid signal cursor.", code="MOBILE_VIDEO_SIGNAL_INVALID")
    return value


@transaction.atomic
def poll_viewer(request, session_id, after):
    session = viewer_session(request, session_id, lock=True)
    if session.state not in TERMINAL:
        session.viewer_seen_at = timezone.now()
        session.save(update_fields=["viewer_seen_at", "updated_at"])
    return payload(session, role="viewer", after=cursor(after))


@transaction.atomic
def poll_device(request, device_id, session_id=None, after=0):
    from .services import authenticate_device_request, MobileNotFound
    device = authenticate_device_request(request=request, device_id=device_id)
    if session_id is not None:
        try:
            if not isinstance(session_id, str):
                raise ValueError()
            UUID(session_id)
        except (ValueError, TypeError, AttributeError):
            raise MobileVideoError("Invalid video session.", code="MOBILE_VIDEO_SIGNAL_INVALID") from None
    qs = device.video_sessions.select_related("device").select_for_update(of=("self",))
    session = qs.filter(id=session_id).first() if session_id else qs.exclude(state__in=TERMINAL).order_by("created_at").first()
    if not session:
        if session_id:
            raise MobileNotFound()
        return {"session": None}
    check_session(session)
    if session_id:
        session.device_seen_at = timezone.now()
        session.save(update_fields=["device_seen_at", "updated_at"])
    return {"session": payload(session, role="device", after=cursor(after), include_ice=not session_id)}


@transaction.atomic
def signal_session(request, session_id, data, *, role):
    from .services import authenticate_device_request, MobileNotFound
    if role == "viewer":
        session = viewer_session(request, session_id, lock=True)
    else:
        session = MobileVideoSession.objects.select_related("device").select_for_update(of=("self",)).filter(id=session_id).first()
        if not session:
            raise MobileNotFound()
        authenticate_device_request(request=request, device_id=str(session.device_id))
        check_session(session)
    if not isinstance(data, dict):
        raise MobileVideoError("Invalid video signal.", code="MOBILE_VIDEO_SIGNAL_INVALID")
    previous = (session.state, session.geometry, session.error_code)
    kind = data.get("type")
    allowed = {"offer", "ice", "stop"} if role == "viewer" else {"answer", "ice", "state", "stop"}
    if kind not in allowed or len(json.dumps(data, ensure_ascii=True)) > 70000:
        raise MobileVideoError("Invalid video signal.", code="MOBILE_VIDEO_SIGNAL_INVALID")
    message_id = str(data.get("message_id", ""))
    try:
        UUID(message_id)
    except (ValueError, TypeError):
        raise MobileVideoError("Signal ID is required.", code="MOBILE_VIDEO_SIGNAL_INVALID") from None
    if session.state in TERMINAL:
        if kind == "stop":
            return payload(session, role=role)
        raise MobileVideoError()
    if any(s["id"] == message_id and s["role"] == role for s in session.signals):
        return payload(session, role=role)
    signal = {"type": kind}
    if kind in {"offer", "answer"}:
        sdp = data.get("sdp")
        if not isinstance(sdp, str) or not sdp.startswith("v=0") or len(sdp) > 65536:
            raise MobileVideoError("Invalid SDP.", code="MOBILE_VIDEO_SIGNAL_INVALID")
        if any(s["type"] == kind for s in session.signals):
            raise MobileVideoError("Renegotiation requires a new video session.")
        if kind == "answer" and not any(s["type"] == "offer" for s in session.signals):
            raise MobileVideoError("Video offer is required before answering.")
        signal["sdp"] = sdp
        session.state = "connecting"
    elif kind == "ice":
        candidate = data.get("candidate")
        if (not isinstance(candidate, dict) or not isinstance(candidate.get("candidate"), str)
            or len(candidate["candidate"]) > 4096
            or not isinstance(candidate.get("sdpMid"), (str, type(None)))
            or len(candidate.get("sdpMid") or "") > 128
            or type(candidate.get("sdpMLineIndex")) is not int
            or not 0 <= candidate["sdpMLineIndex"] <= 16):
            raise MobileVideoError("Invalid ICE candidate.", code="MOBILE_VIDEO_SIGNAL_INVALID")
        signal["candidate"] = {k: candidate.get(k) for k in ("candidate", "sdpMid", "sdpMLineIndex")}
    elif kind == "state":
        state = data.get("state")
        if state not in {"connecting", "live", "failed", "stopped"}:
            raise MobileVideoError("Invalid video state.")
        session.state = state
        geometry = data.get("geometry")
        if state == "live":
            if not any(s["type"] == "answer" for s in session.signals):
                raise MobileVideoError("Video negotiation is not complete.")
            if not isinstance(geometry, dict) or any(type(geometry.get(k)) is not int or not 0 < geometry[k] <= 10000 for k in ("screen_width", "screen_height")) or type(geometry.get("rotation")) is not int or geometry["rotation"] not in range(4):
                raise MobileVideoError("Invalid screen geometry.")
            geometry = {k: geometry[k] for k in ("screen_width", "screen_height", "rotation")}
            if geometry != session.geometry:
                session.geometry = geometry
                session.geometry_version += 1
        error_code = data.get("error_code", "")
        if not isinstance(error_code, str) or len(error_code) > 64 or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for c in error_code):
            raise MobileVideoError("Invalid video error code.")
        session.error_code = error_code
    else:
        session.state = "stopped"
    if kind == "state" and previous == (session.state, session.geometry, session.error_code):
        session.device_seen_at = timezone.now()
        session.save(update_fields=["device_seen_at", "updated_at"])
        return payload(session, role=role)
    if session.state in TERMINAL:
        session.signals = []
        session.save()
        return payload(session, role=role)
    if len(session.signals) >= 96:
        raise MobileVideoError("Too many video signals. Start a new session.")
    session.sequence += 1
    signal.update(role=role, sequence=session.sequence, id=message_id)
    session.signals = [*session.signals, signal]
    if role == "viewer":
        session.viewer_seen_at = timezone.now()
    else:
        session.device_seen_at = timezone.now()
    session.save()
    if session.state in TERMINAL:
        session.signals = []
        session.save(update_fields=["signals"])
    return payload(session, role=role)


def prepare_video_arguments(device, arguments):
    from .control import MobileControlError
    try:
        session = device.video_sessions.filter(id=arguments["video_session_id"]).first()
    except (ValueError, TypeError):
        session = None
    now = timezone.now()
    if not session or check_session(session).state != "live" or not session.device_seen_at or session.device_seen_at < now - timedelta(seconds=5) or arguments.get("video_geometry_version") != session.geometry_version:
        raise MobileControlError("Live video changed or disconnected. Reconnect before controlling the phone.")
    arguments = {**arguments, "expected_screen": session.geometry}
    return arguments
