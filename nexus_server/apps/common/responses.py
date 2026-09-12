from __future__ import annotations

from typing import Any


def unified_response(
    *,
    ok: bool,
    data: Any = None,
    error: dict[str, str] | None = None,
    request_id: str = "",
) -> dict[str, Any]:
    return {
        "ok": ok,
        "data": data if ok else None,
        "error": None if ok else error,
        "request_id": request_id,
    }


def success_response(data: Any = None, request_id: str = "") -> dict[str, Any]:
    return unified_response(ok=True, data=data if data is not None else {}, request_id=request_id)


def error_response(code: str, message: str, request_id: str = "") -> dict[str, Any]:
    return unified_response(
        ok=False,
        error={"code": code, "message": message},
        request_id=request_id,
    )
