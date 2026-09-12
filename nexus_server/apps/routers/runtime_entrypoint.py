from __future__ import annotations

import importlib.util
import inspect
import json
import sys
from pathlib import Path
from typing import Any


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: runtime_entrypoint.py <input.json> <output.json> <router.py>", file=sys.stderr)
        return 64
    input_path = Path(argv[1])
    output_path = Path(argv[2])
    router_path = Path(argv[3])
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        route = load_route(router_path)
        decision = route(payload["request"], payload["candidates"], payload["context"])
        output_path.write_text(
            json.dumps({"ok": True, "decision": decision}, separators=(",", ":")),
            encoding="utf-8",
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - this process boundary must serialize all user-code failures.
        output_path.write_text(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": exc.__class__.__name__.upper(),
                        "message": str(exc),
                    },
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        return 70


def load_route(router_path: Path):
    spec = importlib.util.spec_from_file_location("nexus_uploaded_router", router_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("router.py could not be loaded.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    route = getattr(module, "route", None)
    if route is None or not callable(route):
        raise RuntimeError("router.py must define callable route(request, candidates, context).")
    signature = inspect.signature(route)
    positional = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
    ]
    if len(positional) < 3:
        raise RuntimeError("route() must accept request, candidates, and context.")
    return route


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
