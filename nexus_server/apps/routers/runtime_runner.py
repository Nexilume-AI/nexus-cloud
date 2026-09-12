from __future__ import annotations

import io
import json
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.conf import settings
from rest_framework import exceptions

from .runtime_entrypoint import load_route


@dataclass(frozen=True)
class RouterRuntimeResult:
    decision: dict[str, Any] | None
    latency_ms: int
    exit_code: int | None = None
    error_code: str = ""
    error_message: str = ""


class BaseRouterRuntimeRunner:
    def run(
        self,
        *,
        router_file_path: Path,
        request_payload: dict[str, Any],
        candidates: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> RouterRuntimeResult:
        raise NotImplementedError


class DisabledRouterRuntimeRunner(BaseRouterRuntimeRunner):
    def run(
        self,
        *,
        router_file_path: Path,
        request_payload: dict[str, Any],
        candidates: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> RouterRuntimeResult:
        return RouterRuntimeResult(
            decision=None,
            latency_ms=1,
            error_code="ROUTER_RUNTIME_DISABLED",
            error_message="Custom router runtime is disabled.",
        )


class LocalRouterRuntimeRunner(BaseRouterRuntimeRunner):
    def run(
        self,
        *,
        router_file_path: Path,
        request_payload: dict[str, Any],
        candidates: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> RouterRuntimeResult:
        started = time.monotonic()
        try:
            route = load_route(router_file_path)
            decision = route(request_payload, candidates, context)
            return RouterRuntimeResult(decision=decision, latency_ms=elapsed_ms(started), exit_code=0)
        except Exception as exc:  # noqa: BLE001 - local runner is for tests/dev and mirrors sandbox failure shape.
            return RouterRuntimeResult(
                decision=None,
                latency_ms=elapsed_ms(started),
                exit_code=70,
                error_code=exc.__class__.__name__.upper(),
                error_message=str(exc),
            )


class NsjailRouterRuntimeRunner(BaseRouterRuntimeRunner):
    def run(
        self,
        *,
        router_file_path: Path,
        request_payload: dict[str, Any],
        candidates: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> RouterRuntimeResult:
        nsjail_bin = shutil.which(str(getattr(settings, "NEXUS_NSJAIL_BIN", "nsjail")))
        if not nsjail_bin:
            return RouterRuntimeResult(
                decision=None,
                latency_ms=1,
                error_code="NSJAIL_NOT_FOUND",
                error_message="nsjail executable was not found.",
            )
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="nexus-router-runtime-") as tmp_root:
            work_dir = Path(tmp_root) / "work"
            jail_root = Path(tmp_root) / "rootfs"
            work_dir.mkdir()
            jail_root.mkdir()
            input_path = work_dir / "input.json"
            output_path = work_dir / "output.json"
            jailed_router_path = work_dir / "router.py"
            entrypoint_path = work_dir / "runtime_entrypoint.py"
            input_path.write_text(
                json.dumps(
                    {
                        "request": request_payload,
                        "candidates": candidates,
                        "context": context,
                    },
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            shutil.copyfile(router_file_path, jailed_router_path)
            shutil.copyfile(Path(__file__).with_name("runtime_entrypoint.py"), entrypoint_path)
            command = self._command(
                nsjail_bin=nsjail_bin,
                jail_root=jail_root,
                work_dir=work_dir,
            )
            timeout_seconds = float(getattr(settings, "NEXUS_ROUTER_RUNTIME_TIMEOUT_SECONDS", 2))
            try:
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds + 1,
                )
            except subprocess.TimeoutExpired:
                return RouterRuntimeResult(
                    decision=None,
                    latency_ms=elapsed_ms(started),
                    error_code="ROUTER_RUNTIME_TIMEOUT",
                    error_message="Custom router execution timed out.",
                )
            if not output_path.exists():
                return RouterRuntimeResult(
                    decision=None,
                    latency_ms=elapsed_ms(started),
                    exit_code=completed.returncode,
                    error_code="ROUTER_RUNTIME_FAILED",
                    error_message=(completed.stderr or completed.stdout or "Custom router produced no output.")[:512],
                )
            try:
                output = json.loads(output_path.read_text(encoding="utf-8"))
            except ValueError as exc:
                return RouterRuntimeResult(
                    decision=None,
                    latency_ms=elapsed_ms(started),
                    exit_code=completed.returncode,
                    error_code="ROUTER_RUNTIME_INVALID_OUTPUT",
                    error_message=str(exc),
                )
            if completed.returncode != 0 or not output.get("ok"):
                error = output.get("error") if isinstance(output, dict) else {}
                return RouterRuntimeResult(
                    decision=None,
                    latency_ms=elapsed_ms(started),
                    exit_code=completed.returncode,
                    error_code=str(error.get("code") or "ROUTER_RUNTIME_FAILED")[:64],
                    error_message=str(error.get("message") or "Custom router execution failed.")[:512],
                )
            return RouterRuntimeResult(
                decision=output.get("decision"),
                latency_ms=elapsed_ms(started),
                exit_code=completed.returncode,
            )

    def _command(self, *, nsjail_bin: str, jail_root: Path, work_dir: Path) -> list[str]:
        command = [
            nsjail_bin,
            "-Mo",
            "--quiet",
            "--time_limit",
            str(int(getattr(settings, "NEXUS_ROUTER_RUNTIME_TIMEOUT_SECONDS", 2))),
            "--rlimit_as",
            str(int(getattr(settings, "NEXUS_ROUTER_RUNTIME_MEMORY_MB", 128))),
            "--rlimit_cpu",
            str(int(getattr(settings, "NEXUS_ROUTER_RUNTIME_CPU_SECONDS", 1))),
            "--rlimit_nproc",
            str(int(getattr(settings, "NEXUS_ROUTER_RUNTIME_PIDS_LIMIT", 32))),
            "--disable_proc",
            "--chroot",
            str(jail_root),
            "--cwd",
            "/work",
            "--bindmount",
            f"{work_dir}:/work",
        ]
        for bind_path in readonly_bind_paths():
            command.extend(["--bindmount_ro", f"{bind_path}:{bind_path}"])
        command.extend(
            [
                "--env",
                "PYTHONNOUSERSITE=1",
                "--env",
                "PYTHONDONTWRITEBYTECODE=1",
                "--",
                str(getattr(settings, "NEXUS_ROUTER_RUNTIME_PYTHON", "/usr/bin/python3")),
                "/work/runtime_entrypoint.py",
                "/work/input.json",
                "/work/output.json",
                "/work/router.py",
            ]
        )
        return command


class DockerNsjailRouterRuntimeRunner(NsjailRouterRuntimeRunner):
    def run(
        self,
        *,
        router_file_path: Path,
        request_payload: dict[str, Any],
        candidates: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> RouterRuntimeResult:
        if not shutil.which("docker"):
            return RouterRuntimeResult(
                decision=None,
                latency_ms=1,
                error_code="DOCKER_NOT_FOUND",
                error_message="docker executable was not found.",
            )
        started = time.monotonic()
        input_payload = json.dumps(
            {
                "request": request_payload,
                "candidates": candidates,
                "context": context,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        command = self._docker_command()
        timeout_seconds = float(getattr(settings, "NEXUS_ROUTER_RUNTIME_TIMEOUT_SECONDS", 2))
        try:
            completed = subprocess.run(
                command,
                input=runtime_tarball(
                    {
                        "input.json": input_payload,
                        "router.py": router_file_path.read_bytes(),
                        "runtime_entrypoint.py": Path(__file__).with_name("runtime_entrypoint.py").read_bytes(),
                    }
                ),
                check=False,
                capture_output=True,
                timeout=timeout_seconds + 5,
            )
        except subprocess.TimeoutExpired:
            return RouterRuntimeResult(
                decision=None,
                latency_ms=elapsed_ms(started),
                error_code="ROUTER_RUNTIME_TIMEOUT",
                error_message="Docker nsjail custom router execution timed out.",
            )
        return parse_runtime_stdout(
            returncode=completed.returncode,
            stdout=completed.stdout.decode("utf-8", errors="replace"),
            stderr=completed.stderr.decode("utf-8", errors="replace"),
            started=started,
        )

    def _docker_command(self) -> list[str]:
        image = str(getattr(settings, "NEXUS_ROUTER_RUNTIME_DOCKER_NSJAIL_IMAGE", "nsjail-test"))
        command = [
            "docker",
            "run",
            "--rm",
            "--privileged",
            "--network",
            "none",
            "-i",
            image,
        ]
        nsjail_command = " ".join(shlex.quote(part) for part in self._container_nsjail_command())
        command.extend(
            [
                "/bin/sh",
                "-c",
                f"mkdir -p /work && tar -xf - -C /work; {nsjail_command}; status=$?; "
                "cat /work/output.json 2>/dev/null || true; exit $status",
            ]
        )
        return command

    def _container_nsjail_command(self) -> list[str]:
        return [
            str(getattr(settings, "NEXUS_NSJAIL_BIN", "nsjail")),
            "-Mo",
            "--quiet",
            "--time_limit",
            str(int(getattr(settings, "NEXUS_ROUTER_RUNTIME_TIMEOUT_SECONDS", 2))),
            "--rlimit_as",
            str(int(getattr(settings, "NEXUS_ROUTER_RUNTIME_MEMORY_MB", 128))),
            "--rlimit_cpu",
            str(int(getattr(settings, "NEXUS_ROUTER_RUNTIME_CPU_SECONDS", 1))),
            "--rlimit_nproc",
            str(int(getattr(settings, "NEXUS_ROUTER_RUNTIME_PIDS_LIMIT", 32))),
            "--disable_proc",
            "--chroot",
            "/",
            "--cwd",
            "/work",
            "--bindmount",
            "/work:/work",
            "--env",
            "PYTHONNOUSERSITE=1",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--",
            str(getattr(settings, "NEXUS_ROUTER_RUNTIME_PYTHON", "/usr/bin/python3")),
            "/work/runtime_entrypoint.py",
            "/work/input.json",
            "/work/output.json",
            "/work/router.py",
        ]


def parse_runtime_output(
    *,
    output_path: Path,
    returncode: int,
    stdout: str,
    stderr: str,
    started: float,
) -> RouterRuntimeResult:
    if not output_path.exists():
        return RouterRuntimeResult(
            decision=None,
            latency_ms=elapsed_ms(started),
            exit_code=returncode,
            error_code="ROUTER_RUNTIME_FAILED",
            error_message=(stderr or stdout or "Custom router produced no output.")[:512],
        )
    try:
        output = json.loads(output_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return RouterRuntimeResult(
            decision=None,
            latency_ms=elapsed_ms(started),
            exit_code=returncode,
            error_code="ROUTER_RUNTIME_INVALID_OUTPUT",
            error_message=str(exc),
        )
    if returncode != 0 or not output.get("ok"):
        error = output.get("error") if isinstance(output, dict) else {}
        return RouterRuntimeResult(
            decision=None,
            latency_ms=elapsed_ms(started),
            exit_code=returncode,
            error_code=str(error.get("code") or "ROUTER_RUNTIME_FAILED")[:64],
            error_message=str(error.get("message") or "Custom router execution failed.")[:512],
        )
    return RouterRuntimeResult(
        decision=output.get("decision"),
        latency_ms=elapsed_ms(started),
        exit_code=returncode,
    )


def parse_runtime_stdout(*, returncode: int, stdout: str, stderr: str, started: float) -> RouterRuntimeResult:
    output_text = stdout.strip()
    if not output_text:
        return RouterRuntimeResult(
            decision=None,
            latency_ms=elapsed_ms(started),
            exit_code=returncode,
            error_code="ROUTER_RUNTIME_FAILED",
            error_message=(stderr or "Custom router produced no output.")[:512],
        )
    try:
        output = json.loads(output_text)
    except ValueError as exc:
        return RouterRuntimeResult(
            decision=None,
            latency_ms=elapsed_ms(started),
            exit_code=returncode,
            error_code="ROUTER_RUNTIME_INVALID_OUTPUT",
            error_message=str(exc),
        )
    if returncode != 0 or not output.get("ok"):
        error = output.get("error") if isinstance(output, dict) else {}
        return RouterRuntimeResult(
            decision=None,
            latency_ms=elapsed_ms(started),
            exit_code=returncode,
            error_code=str(error.get("code") or "ROUTER_RUNTIME_FAILED")[:64],
            error_message=str(error.get("message") or "Custom router execution failed.")[:512],
        )
    return RouterRuntimeResult(
        decision=output.get("decision"),
        latency_ms=elapsed_ms(started),
        exit_code=returncode,
    )


def runtime_tarball(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def readonly_bind_paths() -> list[str]:
    raw_value = str(
        getattr(
            settings,
            "NEXUS_ROUTER_RUNTIME_READONLY_BINDS",
            "/usr,/lib,/lib64,/bin,/etc/ld.so.cache,/etc/alternatives",
        )
    )
    paths = []
    for value in raw_value.split(","):
        path = value.strip()
        if path and Path(path).exists():
            paths.append(path)
    return paths


def get_router_runtime_runner() -> BaseRouterRuntimeRunner:
    runner = str(getattr(settings, "NEXUS_ROUTER_RUNTIME_RUNNER", "nsjail")).lower()
    runners: dict[str, BaseRouterRuntimeRunner] = {
        "nsjail": NsjailRouterRuntimeRunner(),
        "docker-nsjail": DockerNsjailRouterRuntimeRunner(),
        "docker_nsjail": DockerNsjailRouterRuntimeRunner(),
        "local": LocalRouterRuntimeRunner(),
        "fake": LocalRouterRuntimeRunner(),
        "disabled": DisabledRouterRuntimeRunner(),
    }
    if runner not in runners:
        raise exceptions.APIException(f"Unsupported router runtime runner: {runner}")
    return runners[runner]


def elapsed_ms(started: float) -> int:
    return max(int((time.monotonic() - started) * 1000), 1)
