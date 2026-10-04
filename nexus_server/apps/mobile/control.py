"""Device-negotiated actions and short-lived, single-use screen controls."""
from datetime import timedelta
import secrets

from django.utils import timezone
from rest_framework.exceptions import APIException

from .models import MobileCommand

NEW_ACTIONS = {"press_home", "press_recents", "long_press"}
SCREEN_ACTIONS = {"tap_coordinates", "swipe", "long_press"}
FRAME_TTL_SECONDS = 30
FRAME_METADATA_KEY = "screen_control_frame"


class MobileControlError(APIException):
    status_code = 409
    default_code = "MOBILE_SCREEN_STALE"
    default_detail = "Capture a fresh screen before controlling this device."


def available_actions(device):
    known = [action for action, _ in MobileCommand.ACTION_CHOICES]
    advertised = (device.capabilities or {}).get("supported_actions")
    if isinstance(advertised, list):
        return [action for action in known if action in advertised]
    # Existing APKs keep their original command contract, but never gain new actions.
    return [action for action in known if action not in NEW_ACTIONS and (
        action != "capture_screen" or (device.capabilities or {}).get("screenshot") is True
    )]


def screen_frame(device):
    frame = (device.metadata or {}).get(FRAME_METADATA_KEY)
    if not isinstance(frame, dict) or not device.last_screenshot_captured_at or not device.screenshot_available:
        return None
    captured = device.last_screenshot_captured_at
    geometry = frame.get("geometry")
    if (frame.get("captured_at") != captured.isoformat()
        or not isinstance(frame.get("id"), str) or len(frame["id"]) != 64
        or not isinstance(geometry, dict)
        or any(type(geometry.get(key)) is not int or not 0 < geometry[key] <= 10000
               for key in ("screen_width", "screen_height"))
        or type(geometry.get("rotation")) is not int or geometry["rotation"] not in range(4)):
        return None
    expires = captured + timedelta(seconds=FRAME_TTL_SECONDS)
    return {
        "id": frame["id"],
        "expires_at": expires.isoformat(),
        "controllable": bool(
            not frame.get("consumed") and expires > timezone.now()
            and device.lifecycle_status == "online"
            and (device.capabilities or {}).get("screen_control") == 1
        ),
    }


def validate_action(device, action):
    if action in NEW_ACTIONS or isinstance((device.capabilities or {}).get("supported_actions"), list):
        if action not in available_actions(device):
            raise MobileControlError("This phone does not support this action. Update Nexus Mobile and reconnect.", code="MOBILE_ACTION_UNSUPPORTED")


def prepare_screen_arguments(device, action, arguments):
    validate_action(device, action)
    arguments = dict(arguments)
    # Delegate geometry is always server-owned, never accepted from API clients.
    arguments.pop("expected_screen", None)
    if "video_session_id" in arguments:
        if action not in SCREEN_ACTIONS or "screen_frame_id" in arguments:
            raise MobileControlError()
        from .video import prepare_video_arguments
        return prepare_video_arguments(device, arguments)
    if "screen_frame_id" not in arguments:
        return arguments
    frame = screen_frame(device)
    if action not in SCREEN_ACTIONS or not frame or not frame["controllable"] or not secrets.compare_digest(
        str(arguments["screen_frame_id"]), str(frame["id"])
    ):
        raise MobileControlError()
    arguments["expected_screen"] = dict(device.metadata[FRAME_METADATA_KEY]["geometry"])
    return arguments


def consume_screen_frame(device):
    metadata = dict(device.metadata or {})
    metadata[FRAME_METADATA_KEY] = {**metadata[FRAME_METADATA_KEY], "consumed": True}
    device.metadata = metadata
    device.save(update_fields=["metadata", "updated_at"])


def remember_screen_frame(device, result):
    metadata = dict(device.metadata or {})
    metadata.pop(FRAME_METADATA_KEY, None)
    geometry = {key: result.get(key) for key in ("screen_width", "screen_height", "rotation")}
    if (
        all(type(geometry[key]) is int and 0 < geometry[key] <= 10000 for key in ("screen_width", "screen_height"))
        and type(geometry["rotation"]) is int and geometry["rotation"] in range(4)
    ):
        metadata[FRAME_METADATA_KEY] = {
            "id": secrets.token_hex(32), "captured_at": device.last_screenshot_captured_at.isoformat(),
            "geometry": geometry, "consumed": False,
        }
    device.metadata = metadata
