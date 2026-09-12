from __future__ import annotations

import base64
import hashlib
import json
import queue
import re
import secrets
import select
import shlex
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import close_old_connections
from django.utils import timezone
from rest_framework import exceptions

from apps.common.subjects import hash_token
from apps.workspaces.models import WorkspaceConnection
from apps.workspaces.services import (
    ParamikoWorkspaceRunner,
    cached_workspace_facts,
    normalize_remote_command_output,
    redact_workspace_output,
    remote_home_path,
    windows_powershell_command,
    workspace_platform_name,
    workspace_runner,
)

from .models import AgentBrowserSession, AgentDisplayAsset, AgentDisplayRun, AgentDisplayEvent


from .browser_runtime import AttachedBrowserError


from .browser_runtime import AttachedBrowserComputerRequired


from .browser_runtime import AttachedBrowserPermissionRequired


from .browser_runtime import AttachedBrowserTunnelUnavailable


from .browser_runtime import AttachedBrowserSessionLost


from .browser_runtime import AttachedBrowserActionFailed


_DOM_SCRIPT = r"""
() => {
  const candidates = Array.from(document.querySelectorAll(
    'a,button,input,textarea,select,option,[role],[contenteditable="true"],summary,label,h1,h2,h3,h4,h5,h6,p,li,td,th'
  ));
  const nodes = [];
  window.__nexusAttachedCounter = window.__nexusAttachedCounter || 0;
  for (const element of candidates) {
    if (nodes.length >= 2000) break;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    if (rect.width <= 0 || rect.height <= 0 || style.visibility === 'hidden' || style.display === 'none') continue;
    let ref = element.getAttribute('data-nexus-browser-ref');
    if (!ref) {
      ref = 'e' + (++window.__nexusAttachedCounter);
      element.setAttribute('data-nexus-browser-ref', ref);
    }
    const tag = element.tagName.toLowerCase();
    const role = element.getAttribute('role') || ({a:'link',button:'button',input:'textbox',textarea:'textbox',select:'combobox',option:'option'}[tag] || '');
    const type = (element.getAttribute('type') || '').toLowerCase();
    const key = `${element.getAttribute('name') || ''} ${element.getAttribute('autocomplete') || ''}`;
    const sensitive = type === 'password' || type === 'hidden' || /(token|secret|password|passwd|authorization|cookie|session|api[-_]?key)/i.test(key);
    const rawText = (element.innerText || element.textContent || '').replace(/\s+/g, ' ').trim();
    const value = !sensitive && 'value' in element ? String(element.value || '') : '';
    const name = element.getAttribute('aria-label') || element.getAttribute('alt') || element.getAttribute('placeholder') || rawText || value;
    const nullable = (attribute) => element.hasAttribute(attribute) ? element.getAttribute(attribute) === 'true' : null;
    nodes.push({
      ref, tag, role,
      name: sensitive ? '[REDACTED]' : String(name || '').slice(0, 500),
      text: sensitive ? '[REDACTED]' : String(rawText || value || '').slice(0, 1000),
      selector: '[data-nexus-browser-ref="' + ref + '"]',
      bounds: [rect.x, rect.y, rect.width, rect.height],
      disabled: Boolean(element.disabled) || element.getAttribute('aria-disabled') === 'true',
      checked: 'checked' in element ? Boolean(element.checked) : nullable('aria-checked'),
      selected: 'selected' in element ? Boolean(element.selected) : nullable('aria-selected'),
      expanded: nullable('aria-expanded')
    });
  }
  return {nodes, truncated: candidates.length > 2000};
}
"""


from .browser_runtime import _safe_http_url


from .browser_runtime import _safe_display_url


class _LoopbackForwarder:
    def __init__(self, *, transport, remote_port: int) -> None:
        self.transport = transport
        self.remote_port = int(remote_port)
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(8)
        self.listener.settimeout(0.25)
        self.port = int(self.listener.getsockname()[1])
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._serve, name="nexus-browser-tunnel", daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while not self.stopped.is_set():
            try:
                client, address = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(
                target=self._pipe,
                args=(client, address),
                name="nexus-browser-tunnel-connection",
                daemon=True,
            ).start()

    def _pipe(self, client: socket.socket, address) -> None:
        channel = None
        try:
            channel = self.transport.open_channel(
                "direct-tcpip",
                ("127.0.0.1", self.remote_port),
                (str(address[0]), int(address[1])),
            )
            while not self.stopped.is_set():
                readable, _, _ = select.select([client, channel], [], [], 0.5)
                if client in readable:
                    data = client.recv(65536)
                    if not data:
                        break
                    channel.sendall(data)
                if channel in readable:
                    data = channel.recv(65536)
                    if not data:
                        break
                    client.sendall(data)
        except Exception:
            return
        finally:
            try:
                client.close()
            finally:
                if channel is not None:
                    channel.close()

    def close(self) -> None:
        self.stopped.set()
        self.listener.close()


def _read_browser_launch_details(channel, *, timeout_seconds: float = 30) -> dict[str, Any]:
    """Read the launch marker without closing the long-lived SSH exec channel.

    Windows OpenSSH owns processes started by an exec session. Returning from
    that session immediately terminates the isolated Chrome process, so the
    launcher waits for Chrome for the lifetime of the Run and this reader only
    consumes its initial JSON marker.
    """

    stdout = bytearray()
    stderr = bytearray()
    deadline = time.monotonic() + max(float(timeout_seconds), 1)
    output_limit = 64 * 1024
    while time.monotonic() < deadline:
        progressed = False
        while channel.recv_ready():
            progressed = True
            stdout.extend(channel.recv(65536))
            if len(stdout) > output_limit:
                raise AttachedBrowserError("Attached Computer browser launcher returned too much output.")
        while channel.recv_stderr_ready():
            progressed = True
            stderr.extend(channel.recv_stderr(65536))
            if len(stderr) > output_limit:
                raise AttachedBrowserError("Attached Computer browser launcher returned too much error output.")
        output = normalize_remote_command_output(bytes(stdout))
        for line in reversed(output.splitlines()):
            if not line.strip().startswith("{"):
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
            returncode = int(channel.recv_exit_status())
            detail = redact_workspace_output(normalize_remote_command_output(bytes(stderr or stdout)))[:240]
            suffix = f": {detail}" if detail else f" (exit {returncode})"
            raise AttachedBrowserError("Unable to start an isolated browser" + suffix)
        if not progressed:
            time.sleep(0.05)
    raise AttachedBrowserTunnelUnavailable("Timed out starting the Attached Computer browser.")


def _remote_browser_cleanup_script(*, remote_pid: int, profile_path: str) -> str:
    profile = str(profile_path).replace("'", "''")
    return f"""
$ErrorActionPreference = 'Stop'
$base = [IO.Path]::GetFullPath((Join-Path $env:USERPROFILE '.nexus\\browser-runs'))
$profile = [IO.Path]::GetFullPath('{profile}')
if ($profile.StartsWith($base + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {{
  $profileArgument = '--user-data-dir=' + $profile
  $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
  $roots = @($processes | Where-Object {{
    $_.CommandLine -and $_.CommandLine.IndexOf($profileArgument, [StringComparison]::OrdinalIgnoreCase) -ge 0
  }})
  $ids = [Collections.Generic.HashSet[int]]::new()
  $pending = [Collections.Generic.Queue[int]]::new()
  foreach ($root in $roots) {{
    [void]$ids.Add([int]$root.ProcessId)
    $pending.Enqueue([int]$root.ProcessId)
  }}
  while ($pending.Count -gt 0) {{
    $parent = $pending.Dequeue()
    foreach ($child in @($processes | Where-Object {{ [int]$_.ParentProcessId -eq $parent }})) {{
      if ($ids.Add([int]$child.ProcessId)) {{ $pending.Enqueue([int]$child.ProcessId) }}
    }}
  }}
  foreach ($processId in @($ids)) {{
    Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
  }}
  $deadline = [DateTime]::UtcNow.AddSeconds(5)
  while (Test-Path -LiteralPath $profile) {{
    try {{
      Remove-Item -LiteralPath $profile -Recurse -Force -ErrorAction Stop
    }} catch {{
      if ([DateTime]::UtcNow -ge $deadline) {{ throw }}
      Start-Sleep -Milliseconds 100
    }}
  }}
}}
"""


def _posix_browser_cleanup_script(*, remote_pid: int, profile_path: str) -> str:
    profile = shlex.quote(str(profile_path))
    return f"""
set -u
profile={profile}
base="$HOME/.nexus/browser-runs"
case "$profile" in
  "$base"/*) ;;
  *) echo 'Refusing to clean a browser profile outside the Nexus run directory' >&2; exit 2 ;;
esac
[ "$profile" != "$base" ] || exit 2
snapshot=$(ps -axo pid=,ppid=,command=)
root_pid={int(remote_pid)}
root_command=$(printf '%s\\n' "$snapshot" | awk -v wanted="$root_pid" '$1 == wanted {{ $1=""; $2=""; sub(/^[[:space:]]+/, ""); print; exit }}')
case "$root_command" in
  *"--user-data-dir=$profile"*) ;;
  *) root_pid='' ;;
esac
pids="$root_pid"
pending="$root_pid"
while [ -n "$pending" ]; do
  next=''
  for parent in $pending; do
    children=$(printf '%s\\n' "$snapshot" | awk -v wanted="$parent" '$2 == wanted {{ print $1 }}')
    for child in $children; do
      case " $pids " in *" $child "*) ;; *) pids="$pids $child"; next="$next $child" ;; esac
    done
  done
  pending="$next"
done
if [ -n "$(printf '%s' "$pids" | tr -d ' ')" ]; then
  kill -TERM $pids 2>/dev/null || true
  deadline=$(( $(date +%s) + 5 ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    alive=''
    for process_id in $pids; do
      kill -0 "$process_id" 2>/dev/null && alive="$alive $process_id"
    done
    [ -z "$alive" ] && break
    sleep 0.1
  done
  for process_id in $pids; do
    kill -0 "$process_id" 2>/dev/null && kill -KILL "$process_id" 2>/dev/null || true
  done
fi
deadline=$(( $(date +%s) + 5 ))
while [ -e "$profile" ]; do
  rm -rf -- "$profile" 2>/dev/null || true
  [ ! -e "$profile" ] && break
  [ "$(date +%s)" -ge "$deadline" ] && {{ echo 'Unable to remove the Nexus browser profile' >&2; exit 3; }}
  sleep 0.1
done
"""


def _browser_cleanup_command(*, platform: str, remote_pid: int, profile_path: str) -> str:
    if platform == AgentBrowserSession.PLATFORM_WINDOWS:
        return windows_powershell_command(
            _remote_browser_cleanup_script(
                remote_pid=remote_pid,
                profile_path=profile_path,
            )
        )
    if platform in {AgentBrowserSession.PLATFORM_LINUX, AgentBrowserSession.PLATFORM_MACOS}:
        return "sh -lc " + shlex.quote(
            _posix_browser_cleanup_script(
                remote_pid=remote_pid,
                profile_path=profile_path,
            )
        )
    raise AttachedBrowserError("The Attached Computer platform is not supported for browser cleanup.")


def _windows_browser_launch_script(*, run_key: str) -> str:
    return f"""
$ErrorActionPreference = 'Stop'
$programFilesX86 = [Environment]::GetFolderPath('ProgramFilesX86')
$candidates = @(
  @{{ name = 'chrome'; path = (Join-Path $env:ProgramFiles 'Google\\Chrome\\Application\\chrome.exe') }},
  @{{ name = 'chrome'; path = (Join-Path $programFilesX86 'Google\\Chrome\\Application\\chrome.exe') }},
  @{{ name = 'chrome'; path = (Join-Path $env:LOCALAPPDATA 'Google\\Chrome\\Application\\chrome.exe') }},
  @{{ name = 'edge'; path = (Join-Path $env:ProgramFiles 'Microsoft\\Edge\\Application\\msedge.exe') }},
  @{{ name = 'edge'; path = (Join-Path $programFilesX86 'Microsoft\\Edge\\Application\\msedge.exe') }},
  @{{ name = 'edge'; path = (Join-Path $env:LOCALAPPDATA 'Microsoft\\Edge\\Application\\msedge.exe') }}
)
$browser = $candidates | Where-Object {{ Test-Path -LiteralPath $_.path }} | Select-Object -First 1
if (-not $browser) {{ throw 'Chrome or Microsoft Edge is not installed' }}
$base = Join-Path $env:USERPROFILE '.nexus\\browser-runs'
$profile = Join-Path $base '{run_key}'
New-Item -ItemType Directory -Force -Path $profile | Out-Null
$arguments = @('--headless=new','--remote-debugging-address=127.0.0.1','--remote-debugging-port=0','--remote-allow-origins=*',"--user-data-dir=$profile",'--no-first-run','--no-default-browser-check','--disable-sync','--disable-background-networking','about:blank')
$process = Start-Process -FilePath $browser.path -ArgumentList $arguments -PassThru -WindowStyle Hidden
$active = Join-Path $profile 'DevToolsActivePort'
$deadline = [DateTime]::UtcNow.AddSeconds(20)
while (-not (Test-Path -LiteralPath $active)) {{
  if ($process.HasExited) {{ throw 'Browser exited before CDP became ready' }}
  if ([DateTime]::UtcNow -gt $deadline) {{ throw 'Timed out waiting for browser CDP' }}
  Start-Sleep -Milliseconds 100
}}
$lines = Get-Content -LiteralPath $active
[Console]::Out.WriteLine((@{{pid=$process.Id;port=[int]$lines[0];profile=$profile;browser=$browser.name}} | ConvertTo-Json -Compress))
[Console]::Out.Flush()
$process.WaitForExit()
"""


def _posix_browser_launch_script(*, platform: str, profile_path: str) -> str:
    profile = shlex.quote(str(profile_path))
    macos = platform == AgentBrowserSession.PLATFORM_MACOS
    home = str(profile_path).split("/.nexus/browser-runs/", 1)[0]
    candidate_values = (
        [
            ("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            ("chrome", f"{home}/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            ("chromium", "/Applications/Chromium.app/Contents/MacOS/Chromium"),
            ("chromium", f"{home}/Applications/Chromium.app/Contents/MacOS/Chromium"),
            ("edge", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            ("edge", f"{home}/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        ]
        if macos
        else [
            ("chrome", "/usr/bin/google-chrome-stable"),
            ("chrome", "/usr/bin/google-chrome"),
            ("chrome", "/opt/google/chrome/google-chrome"),
            ("chrome", "google-chrome-stable"),
            ("chrome", "google-chrome"),
            ("chromium", "/usr/bin/chromium"),
            ("chromium", "/usr/bin/chromium-browser"),
            ("chromium", "chromium"),
            ("chromium", "chromium-browser"),
            ("edge", "/usr/bin/microsoft-edge-stable"),
            ("edge", "/opt/microsoft/msedge/microsoft-edge"),
            ("edge", "microsoft-edge-stable"),
            ("edge", "microsoft-edge"),
        ]
    )
    candidates = " \\\n".join(shlex.quote(f"{name}|{path}") for name, path in candidate_values)
    root_guard = (
        "" if macos else "[ \"$(id -u)\" -ne 0 ] || { echo 'Linux browser sessions require a non-root SSH user' >&2; exit 4; }"
    )
    return f"""
set -eu
umask 077
{root_guard}
profile={profile}
mkdir -p "$profile"
chmod 700 "$profile"
browser=''
browser_name=''
for candidate in {candidates}
do
  candidate_name=${{candidate%%|*}}
  candidate_path=${{candidate#*|}}
  case "$candidate_path" in
    /*) [ -x "$candidate_path" ] || continue ;;
    *) candidate_path=$(command -v "$candidate_path" 2>/dev/null || true); [ -n "$candidate_path" ] || continue ;;
  esac
  browser=$candidate_path
  browser_name=$candidate_name
  break
done
[ -n "$browser" ] || {{ echo 'Chrome, Chromium, or Microsoft Edge is not installed' >&2; exit 5; }}
"$browser" --headless=new --remote-debugging-address=127.0.0.1 --remote-debugging-port=0 --remote-allow-origins='*' --user-data-dir="$profile" --no-first-run --no-default-browser-check --disable-sync --disable-background-networking about:blank >/dev/null 2>&1 &
browser_pid=$!
active="$profile/DevToolsActivePort"
deadline=$(( $(date +%s) + 20 ))
while [ ! -f "$active" ]; do
  kill -0 "$browser_pid" 2>/dev/null || {{ echo 'Browser exited before CDP became ready' >&2; exit 6; }}
  [ "$(date +%s)" -le "$deadline" ] || {{ kill -TERM "$browser_pid" 2>/dev/null || true; echo 'Timed out waiting for browser CDP' >&2; exit 7; }}
  sleep 0.1
done
port=$(sed -n '1p' "$active")
case "$port" in ''|*[!0-9]*) echo 'Browser returned an invalid CDP port' >&2; exit 8 ;; esac
printf '{{"pid":%s,"port":%s,"browser":"%s"}}\n' "$browser_pid" "$port" "$browser_name"
wait "$browser_pid"
"""


@dataclass
class _WorkItem:
    callback: Callable[["_RemoteBrowserWorker"], Any]
    result: queue.Queue


class _RemoteBrowserWorker:
    def __init__(self, *, session_id: str, connection_id: str, viewport: tuple[int, int]) -> None:
        self.session_id = str(session_id)
        self.connection_id = str(connection_id)
        self.viewport = viewport
        self.items: queue.Queue = queue.Queue()
        self.ready = threading.Event()
        self.start_error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, name=f"nexus-attached-browser-{session_id}", daemon=True)
        self.ssh = None
        self.launch_channel = None
        self.forwarder = None
        self.playwright = None
        self.browser = None
        self.page = None
        self.remote_pid = 0
        self.remote_port = 0
        self.profile_path = ""
        self.platform = AgentBrowserSession.PLATFORM_WINDOWS
        self.browser_name = ""
        self.revision = 0
        self.last_used_monotonic = time.monotonic()
        self.thread.start()
        self.ready.wait(45)
        if not self.ready.is_set():
            # If SSH/Chrome eventually returns, the worker consumes this stop
            # sentinel immediately and performs exact PID/Profile cleanup.
            self.items.put(None)
            raise AttachedBrowserTunnelUnavailable("Timed out starting the Attached Computer browser.")
        if self.start_error is not None:
            self._mark_failed(self.start_error)
            raise self.start_error
        AgentBrowserSession.objects.filter(id=self.session_id).update(
            status=AgentBrowserSession.STATUS_ACTIVE,
            remote_pid=self.remote_pid,
            remote_port=self.remote_port,
            profile_path=self.profile_path,
            platform=self.platform,
            last_error="",
        )

    def _run(self) -> None:
        close_old_connections()
        try:
            self._start()
        except BaseException as exc:
            self.start_error = exc
        finally:
            self.ready.set()
        if self.start_error is not None:
            self._cleanup()
            return
        while True:
            item = self.items.get()
            if item is None:
                break
            try:
                item.result.put((True, item.callback(self)))
            except BaseException as exc:
                item.result.put((False, exc))
        self._cleanup()

    def _start(self) -> None:
        connection = WorkspaceConnection.objects.get(id=self.connection_id)
        facts = cached_workspace_facts(connection=connection)
        self.platform = workspace_platform_name(facts)
        if self.platform not in {
            AgentBrowserSession.PLATFORM_WINDOWS,
            AgentBrowserSession.PLATFORM_LINUX,
            AgentBrowserSession.PLATFORM_MACOS,
        }:
            raise AttachedBrowserError(
                "Attached browser supports Windows, Linux, and macOS OpenSSH Computers."
            )
        runner = workspace_runner()
        if not isinstance(runner, ParamikoWorkspaceRunner):
            raise AttachedBrowserError("Attached browser requires the real Paramiko SSH runner.")
        self.ssh = runner.open_ssh_client(connection=connection)
        run_key = re.sub(r"[^a-f0-9-]", "", self.session_id.lower()) + "-" + uuid.uuid4().hex[:12]
        transport = self.ssh.get_transport()
        if transport is None or not transport.is_active():
            raise AttachedBrowserTunnelUnavailable()
        transport.set_keepalive(15)
        try:
            if self.platform == AgentBrowserSession.PLATFORM_WINDOWS:
                command = windows_powershell_command(
                    _windows_browser_launch_script(run_key=run_key)
                )
            else:
                sftp = self.ssh.open_sftp()
                try:
                    home = remote_home_path(sftp=sftp).rstrip("/")
                finally:
                    sftp.close()
                self.profile_path = f"{home}/.nexus/browser-runs/{run_key}"
                command = "sh -lc " + shlex.quote(
                    _posix_browser_launch_script(
                        platform=self.platform,
                        profile_path=self.profile_path,
                    )
                )
            self.launch_channel = transport.open_session(timeout=10)
            self.launch_channel.exec_command(command)
            details = _read_browser_launch_details(self.launch_channel, timeout_seconds=30)
            self.remote_pid = int(details["pid"])
            self.remote_port = int(details["port"])
            self.profile_path = str(details.get("profile") or self.profile_path)
            self.browser_name = str(details.get("browser") or "")
        except AttachedBrowserError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise AttachedBrowserError("Attached Computer returned an invalid browser launch result.") from exc
        except Exception as exc:
            raise AttachedBrowserTunnelUnavailable("Could not start the protected browser launcher.") from exc
        self.forwarder = _LoopbackForwarder(transport=transport, remote_port=self.remote_port)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise AttachedBrowserError("Cloud browser broker requires the Playwright package.") from exc
        # No Django ORM work may run in this thread once Playwright installs
        # its event loop. Release the connection used for the initial lookup;
        # request/reaper threads persist all subsequent session state.
        close_old_connections()
        self.playwright = sync_playwright().start()
        try:
            self.browser = self.playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{self.forwarder.port}", timeout=20000
            )
        except Exception as exc:
            raise AttachedBrowserTunnelUnavailable("Could not establish the protected CDP tunnel.") from exc
        context = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()
        self.page = context.pages[0] if context.pages else context.new_page()
        self.page.set_viewport_size({"width": self.viewport[0], "height": self.viewport[1]})

    def _mark_failed(self, exc: BaseException) -> None:
        AgentBrowserSession.objects.filter(id=self.session_id).update(
            status=AgentBrowserSession.STATUS_FAILED,
            last_error=redact_workspace_output(str(exc) or exc.__class__.__name__)[:512],
            closed_at=timezone.now(),
        )

    def call(self, callback: Callable[["_RemoteBrowserWorker"], Any]) -> Any:
        if self.start_error is not None or not self.thread.is_alive():
            raise AttachedBrowserSessionLost()
        result: queue.Queue = queue.Queue(maxsize=1)
        self.items.put(_WorkItem(callback=callback, result=result))
        try:
            ok, value = result.get(timeout=65)
        except queue.Empty as exc:
            raise AttachedBrowserSessionLost("Attached browser operation timed out.") from exc
        if ok:
            self.last_used_monotonic = time.monotonic()
            return value
        if isinstance(value, exceptions.APIException):
            raise value
        raise AttachedBrowserActionFailed(redact_workspace_output(str(value) or value.__class__.__name__)[:300])

    def stop(self) -> None:
        if self.thread.is_alive():
            self.items.put(None)
            self.thread.join(timeout=10)
        AgentBrowserSession.objects.filter(
            id=self.session_id,
            status__in=[
                AgentBrowserSession.STATUS_STARTING,
                AgentBrowserSession.STATUS_ACTIVE,
            ],
        ).update(
            status=AgentBrowserSession.STATUS_CLOSED,
            closed_at=timezone.now(),
        )

    def _cleanup(self) -> None:
        try:
            if self.browser is not None:
                self.browser.close()
        except Exception:
            pass
        try:
            if self.playwright is not None:
                self.playwright.stop()
        except Exception:
            pass
        if self.forwarder is not None:
            self.forwarder.close()
        if self.ssh is not None:
            try:
                if self.profile_path:
                    runner = workspace_runner()
                    if isinstance(runner, ParamikoWorkspaceRunner):
                        runner.run_client_command(
                            client=self.ssh,
                            command=_browser_cleanup_command(
                                platform=self.platform,
                                remote_pid=self.remote_pid or 0,
                                profile_path=self.profile_path,
                            ),
                            timeout=20,
                        )
            finally:
                if self.launch_channel is not None:
                    self.launch_channel.close()
                self.ssh.close()
        elif self.launch_channel is not None:
            self.launch_channel.close()


_WORKERS: dict[str, _RemoteBrowserWorker] = {}
_WORKERS_LOCK = threading.RLock()
_REAPER_STARTED = False


def _idle_seconds() -> int:
    return max(int(getattr(settings, "NEXUS_ATTACHED_BROWSER_IDLE_SECONDS", 900)), 60)


def _reap_idle_workers() -> None:
    while True:
        try:
            cutoff = time.monotonic() - _idle_seconds()
            stale: list[_RemoteBrowserWorker] = []
            with _WORKERS_LOCK:
                for key, worker in list(_WORKERS.items()):
                    if worker.last_used_monotonic <= cutoff or not worker.thread.is_alive():
                        _WORKERS.pop(key, None)
                        stale.append(worker)
                local_run_ids = set(_WORKERS)
            for worker in stale:
                worker.stop()
            orphaned = (
                AgentBrowserSession.objects.filter(
                    status__in=[
                        AgentBrowserSession.STATUS_STARTING,
                        AgentBrowserSession.STATUS_ACTIVE,
                    ]
                )
                .exclude(run__status=AgentDisplayRun.STATUS_RUNNING)
                .select_related("connection", "run")
                .order_by("started_at")[:100]
            )
            for session in orphaned:
                if str(session.run_id) in local_run_ids:
                    continue
                if not _close_persisted_browser_session(session):
                    AgentBrowserSession.objects.filter(id=session.id).update(
                        status=AgentBrowserSession.STATUS_FAILED,
                        last_error="Attached Computer browser cleanup failed during Cloud recovery.",
                        closed_at=timezone.now(),
                    )
        except Exception:
            # Recovery is best-effort and must never terminate the API process.
            close_old_connections()
        time.sleep(min(max(_idle_seconds() // 4, 30), 120))


def _ensure_reaper() -> None:
    global _REAPER_STARTED
    if _REAPER_STARTED:
        return
    _REAPER_STARTED = True
    threading.Thread(
        target=_reap_idle_workers,
        name="nexus-attached-browser-reaper",
        daemon=True,
    ).start()


def start_attached_browser_recovery() -> None:
    """Start Browser cleanup only from a long-lived Cloud server process."""

    _ensure_reaper()


from .browser_runtime import _validated_viewport


from .browser_runtime import get_browser_delegate_run


def _close_persisted_browser_session(session: AgentBrowserSession) -> bool:
    if session.connection.connection_type != "runtime":
        # Legacy Cloud-initiated SSH must remain inert after the Runtime rollout.
        AgentBrowserSession.objects.filter(id=session.id).update(
            status=AgentBrowserSession.STATUS_FAILED,
            last_error="LEGACY_SSH_DISABLED: pair Nexus Computer Runtime to use Attached Browser.",
            closed_at=timezone.now(),
        )
        return True
    try:
        from apps.workspaces.computer_runtime import execute_runtime_command

        execute_runtime_command(
            connection=session.connection,
            operation="browser.close",
            required_scope="browser.control",
            payload={
                "browser_session_id": session.runtime_session_id or str(session.id),
                "run_id": str(session.run_id),
            },
            timeout_seconds=10,
            display_run_id=str(session.run_id),
        )
    except Exception:
        # An offline Runtime owns the process and performs its own exact stale
        # Run cleanup after reconnect/startup; Cloud must never fall back to SSH.
        return False
    AgentBrowserSession.objects.filter(id=session.id).update(
        status=AgentBrowserSession.STATUS_CLOSED,
        closed_at=timezone.now(),
    )
    return True


def _evict_oldest_inactive_worker(*, connection_id: str, excluded_run_id: str) -> bool:
    candidates = AgentBrowserSession.objects.filter(
        connection_id=connection_id,
        status__in=[AgentBrowserSession.STATUS_STARTING, AgentBrowserSession.STATUS_ACTIVE],
    ).exclude(
        run_id=excluded_run_id,
    ).exclude(
        run__status=AgentDisplayRun.STATUS_RUNNING,
    ).select_related("connection").order_by("last_used_at", "started_at")
    for session in candidates:
        worker = _WORKERS.pop(str(session.run_id), None)
        if worker is not None:
            worker.stop()
            return True
        if _close_persisted_browser_session(session):
            return True
    return False


def _worker_for_run(*, run: AgentDisplayRun, viewport: tuple[int, int]) -> _RemoteBrowserWorker:
    _ensure_reaper()
    key = str(run.id)
    with _WORKERS_LOCK:
        current = _WORKERS.get(key)
        if current is not None:
            if current.viewport != viewport:
                raise AttachedBrowserActionFailed("Browser viewport cannot change during a Run.")
            return current
        active = AgentBrowserSession.objects.filter(
            connection_id=run.computer_binding.connection_id,
            status__in=[AgentBrowserSession.STATUS_STARTING, AgentBrowserSession.STATUS_ACTIVE],
        ).exclude(run=run).count()
        maximum = max(int(getattr(settings, "NEXUS_ATTACHED_BROWSER_MAX_PER_COMPUTER", 2)), 1)
        while active >= maximum and _evict_oldest_inactive_worker(
            connection_id=str(run.computer_binding.connection_id),
            excluded_run_id=key,
        ):
            active = AgentBrowserSession.objects.filter(
                connection_id=run.computer_binding.connection_id,
                status__in=[AgentBrowserSession.STATUS_STARTING, AgentBrowserSession.STATUS_ACTIVE],
            ).exclude(run=run).count()
        if active >= maximum:
            raise AttachedBrowserError("BROWSER_COMPUTER_BUSY: this Computer is already running the maximum browser sessions.")
        connection = run.computer_binding.connection
        platform = workspace_platform_name(cached_workspace_facts(connection=connection))
        if platform not in {
            AgentBrowserSession.PLATFORM_WINDOWS,
            AgentBrowserSession.PLATFORM_LINUX,
            AgentBrowserSession.PLATFORM_MACOS,
        }:
            raise AttachedBrowserError(
                "Attached browser supports Windows, Linux, and macOS OpenSSH Computers."
            )
        session, _created = AgentBrowserSession.objects.update_or_create(
            run=run,
            defaults={
                "connection": connection,
                "platform": platform,
                "status": AgentBrowserSession.STATUS_STARTING,
                "last_error": "",
                "closed_at": None,
            },
        )
        worker = _RemoteBrowserWorker(
            session_id=str(session.id),
            connection_id=str(session.connection_id),
            viewport=viewport,
        )
        _WORKERS[key] = worker
        return worker


def _capture_observation(*, worker: _RemoteBrowserWorker) -> dict[str, Any]:
    page = worker.page
    worker.revision += 1
    image = bytes(page.screenshot(type="jpeg", quality=70))
    if not image or len(image) > 2 * 1024 * 1024:
        raise AttachedBrowserActionFailed("Browser screenshot exceeded the 2 MiB limit.")
    dom = page.evaluate(_DOM_SCRIPT)
    if not isinstance(dom, dict):
        dom = {"nodes": [], "truncated": False}
    return {
        "image": image,
        "dom": dom,
        "url": str(page.url or ""),
        "title": str(page.title() or "")[:512],
        "revision": worker.revision,
    }


def _store_observation(
    *,
    run_id: str,
    worker: _RemoteBrowserWorker,
    action: str,
    capture: dict[str, Any] | None = None,
) -> dict[str, Any]:
    captured = capture if capture is not None else _capture_observation(worker=worker)
    image = bytes(captured["image"])
    dom = captured["dom"] if isinstance(captured.get("dom"), dict) else {"nodes": [], "truncated": False}
    revision = int(captured["revision"])
    page_url = str(captured.get("url") or "")
    page_title = str(captured.get("title") or "")[:512]
    run = AgentDisplayRun.objects.select_related("agent", "computer_binding__connection").get(id=run_id)
    asset = AgentDisplayAsset(
        run=run,
        content_type="image/jpeg",
        size_bytes=len(image),
        sha256=hashlib.sha256(image).hexdigest(),
        width=worker.viewport[0],
        height=worker.viewport[1],
    )
    asset.file.save("frame.jpg", ContentFile(image), save=True)
    asset_url = f"/api/v1/agent-runs/{run.id}/display-assets/{asset.id}/"
    observation_id = uuid.uuid4().hex
    from .runtime_services import append_display_event
    append_display_event(
        run=run,
        event_type=AgentDisplayEvent.TYPE_COMPUTER_FRAME,
        payload={
            "name": "nexus.computer.frame",
            "value": {
                "frame_id": str(asset.id),
                "screenshot_url": asset_url,
                "url": _safe_display_url(page_url)[:2048],
                "title": page_title,
                "text": f"Attached Computer browser observation {revision}",
                "width": worker.viewport[0],
                "height": worker.viewport[1],
                "observation_id": observation_id,
                "revision": revision,
                "action": str(action)[:64],
                "action_status": "succeeded",
                "dom_node_count": len(dom.get("nodes") or []),
                "source": "attached_computer",
                "computer_name": run.computer_binding.connection.name,
                "profile": "isolated",
            },
        },
        visibility="private",
    )
    AgentBrowserSession.objects.filter(id=worker.session_id).update(
        observation_revision=revision,
        last_used_at=timezone.now(),
    )
    return {
        "observation_id": observation_id,
        "revision": revision,
        "url": page_url,
        "title": page_title,
        "viewport": list(worker.viewport),
        "image_base64": base64.b64encode(image).decode("ascii"),
        "content_type": "image/jpeg",
        "dom": dom,
        "html": "",
        "frame_published": True,
        "computer_name": run.computer_binding.connection.name,
    }


from .browser_runtime import _runtime_browser_session


from .browser_runtime import _store_runtime_observation


from .browser_runtime import browser_delegate_operation


def _legacy_browser_delegate_operation(*, run_id: str, token: str, data: dict[str, Any]) -> dict[str, Any]:
    run = get_browser_delegate_run(run_id=run_id, token=token)
    operation = str(data.get("operation") or "").strip().lower()
    if operation == "close":
        close_attached_browser_session(run_id=str(run.id))
        return {"closed": True}
    worker = _worker_for_run(run=run, viewport=_validated_viewport(data.get("viewport")))

    def execute(active: _RemoteBrowserWorker):
        page = active.page
        action_name = operation
        try:
            if operation == "open":
                timeout = min(max(float(data.get("timeout") or 30), 1), 60)
                page.goto(_safe_http_url(data.get("url")), wait_until="domcontentloaded", timeout=int(timeout * 1000))
                action_name = "navigate"
            elif operation == "observe":
                pass
            elif operation == "action":
                action = data.get("action") if isinstance(data.get("action"), dict) else {}
                expected = action.get("expected_revision")
                if expected is not None and int(expected) != active.revision:
                    raise AttachedBrowserActionFailed("Browser observation is stale; observe the page again.")
                kind = str(action.get("kind") or "").strip().lower()
                action_name = kind
                parameters = action.get("parameters") if isinstance(action.get("parameters"), dict) else {}
                timeout_ms = int(min(max(float(parameters.get("timeout") or 30), 1), 60) * 1000)
                if kind in {"locator_click", "fill", "select"}:
                    target = str(parameters.get("target") or "")
                    selector = f'[data-nexus-browser-ref="{target}"]' if parameters.get("is_ref") else target
                    locator = page.locator(selector)
                    if kind == "locator_click":
                        locator.click(timeout=timeout_ms)
                    elif kind == "fill":
                        locator.fill(str(parameters.get("value") or ""), timeout=timeout_ms)
                    else:
                        locator.select_option(str(parameters.get("value") or ""), timeout=timeout_ms)
                elif kind == "click":
                    x = float(parameters["x"])
                    y = float(parameters["y"])
                    if not 0 <= x < active.viewport[0] or not 0 <= y < active.viewport[1]:
                        raise AttachedBrowserActionFailed("Browser coordinates are outside the viewport.")
                    page.mouse.click(x, y)
                elif kind == "scroll":
                    page.mouse.wheel(float(parameters.get("delta_x") or 0), float(parameters.get("delta_y") or 0))
                elif kind == "type":
                    page.keyboard.type(str(parameters.get("text") or ""))
                elif kind == "drag":
                    from_x = float(parameters["from_x"])
                    from_y = float(parameters["from_y"])
                    to_x = float(parameters["to_x"])
                    to_y = float(parameters["to_y"])
                    if not all((
                        0 <= from_x < active.viewport[0],
                        0 <= to_x < active.viewport[0],
                        0 <= from_y < active.viewport[1],
                        0 <= to_y < active.viewport[1],
                    )):
                        raise AttachedBrowserActionFailed("Browser coordinates are outside the viewport.")
                    page.mouse.move(from_x, from_y)
                    page.mouse.down()
                    page.mouse.move(to_x, to_y, steps=min(max(int(parameters.get("steps") or 10), 1), 100))
                    page.mouse.up()
                elif kind == "reload":
                    page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
                else:
                    raise AttachedBrowserActionFailed("Unsupported browser action.")
            else:
                raise AttachedBrowserActionFailed("Unsupported browser operation.")
            return {
                "action": action_name,
                "capture": _capture_observation(worker=active),
            }
        except exceptions.APIException:
            raise
        except Exception as exc:
            if page.is_closed() or active.browser is None or not active.browser.is_connected():
                raise AttachedBrowserSessionLost() from None
            if operation == "action" and action_name in {"fill", "type", "select"}:
                # Playwright call logs can include the submitted value. Keep
                # sensitive input out of API errors, Agent Chat and Server logs.
                raise AttachedBrowserActionFailed() from None
            raise AttachedBrowserActionFailed(redact_workspace_output(str(exc) or exc.__class__.__name__)[:300]) from None

    try:
        captured = worker.call(execute)
        return _store_observation(
            run_id=str(run.id),
            worker=worker,
            action=str(captured["action"]),
            capture=captured["capture"],
        )
    except AttachedBrowserSessionLost:
        AgentBrowserSession.objects.filter(id=worker.session_id).update(
            status=AgentBrowserSession.STATUS_FAILED,
            last_error="Attached Computer browser session was lost.",
            closed_at=timezone.now(),
        )
        close_attached_browser_session(run_id=str(run.id))
        raise


def _close_legacy_attached_browser_session(*, run_id: str) -> None:
    with _WORKERS_LOCK:
        worker = _WORKERS.pop(str(run_id), None)
    if worker is not None:
        worker.stop()
    else:
        session = AgentBrowserSession.objects.filter(
            run_id=run_id,
            status__in=[AgentBrowserSession.STATUS_STARTING, AgentBrowserSession.STATUS_ACTIVE],
        ).select_related("connection").first()
        if session is not None and not _close_persisted_browser_session(session):
            AgentBrowserSession.objects.filter(id=session.id).update(
                status=AgentBrowserSession.STATUS_FAILED,
                last_error="Attached Computer browser cleanup failed after the Cloud worker was lost.",
                closed_at=timezone.now(),
            )


from .browser_runtime import close_attached_browser_session
