"""Fail-closed production inputs. Never read the Enterprise .local configuration."""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured


def invalid(message):
    raise ImproperlyConfigured("PERSONAL_HOST_CONFIGURATION: " + message) from None


def validate_config(config):
    if not isinstance(config, dict) or type(config.get("schema_version")) is not int or config.get("schema_version") != 1:
        invalid("Use a schema_version 1 configuration object.")
    required = {"schema_version", "instance_id", "public_origin", "state_dir", "secret_key",
                "encryption_key", "database", "redis_url"}
    if set(config) - {"controllers", "monitoring", "python_builder"} != required:
        invalid("Provide exactly the documented production configuration fields.")
    try:
        instance = str(UUID(config["instance_id"]))
        origin = urlsplit(config["public_origin"])
        if (origin.scheme != "https" or not origin.hostname or origin.username is not None
                or origin.password is not None or origin.path not in ("", "/") or origin.query or origin.fragment):
            raise ValueError()
        port = origin.port
        host = origin.hostname.encode("idna").decode("ascii").lower()
        if not re.fullmatch(r"[a-z0-9.-]+|[a-f0-9:]+", host) or "*" in host:
            raise ValueError()
        authority = f"[{host}]" if ":" in host else host
        if port is not None and port != 443:
            authority += f":{port}"
    except (ValueError, TypeError, AttributeError, UnicodeError):
        invalid("public_origin must be one HTTPS origin and instance_id a UUID.")
    if (not isinstance(config["secret_key"], str) or len(config["secret_key"]) < 50
            or len(set(config["secret_key"])) < 8 or len(config["secret_key"]) > 256):
        invalid("Use an independently generated Django secret of at least 50 characters.")
    try:
        Fernet(config["encryption_key"].encode("ascii"))
    except (ValueError, TypeError, AttributeError, UnicodeError):
        invalid("encryption_key must be an independently generated Fernet key.")
    database = config["database"]
    if not isinstance(database, dict) or set(database) != {"name", "host", "port", "user", "password", "sslmode"}:
        invalid("Provide the documented PostgreSQL connection fields.")
    if not all(isinstance(database[key], str) and database[key] for key in ("name", "host", "user", "password", "sslmode")):
        invalid("PostgreSQL connection fields must be nonempty strings.")
    if not re.fullmatch(r"nexus_personal_[a-z0-9_]{1,40}", database["name"]):
        invalid("Use a dedicated PostgreSQL database named nexus_personal_<name>.")
    if type(database["port"]) is not int or not 1 <= database["port"] <= 65535:
        invalid("PostgreSQL port must be an integer between 1 and 65535.")
    if database["sslmode"] not in {"disable", "require", "verify-full"}:
        invalid("Choose an explicit PostgreSQL TLS mode.")
    if database["host"] not in {"localhost", "127.0.0.1", "::1"} and database["sslmode"] != "verify-full":
        invalid("Non-loopback PostgreSQL connections require verify-full TLS.")
    try:
        redis = urlsplit(config["redis_url"])
        if (redis.scheme not in {"redis", "rediss"} or not redis.hostname or redis.query or redis.fragment
                or not re.fullmatch(r"/[1-9][0-9]*", redis.path) or not 0 < int(redis.path[1:]) <= 15):
            raise ValueError()
        if redis.port is not None and not 1 <= redis.port <= 65535:
            raise ValueError()
        if redis.hostname not in {"localhost", "127.0.0.1", "::1"} and redis.scheme != "rediss":
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        invalid("Use a dedicated Redis database 1–15; non-loopback Redis requires TLS.")
    try:
        root = Path(config["state_dir"])
        if not root.is_absolute() or not root.is_dir() or root.is_symlink():
            raise ValueError()
        root = root.resolve(strict=True)
        source = Path(__file__).resolve().parents[1]
        if root == Path(root.anchor) or root == Path.home().resolve() or root == source or root in source.parents or source in root.parents:
            raise ValueError()
    except (ValueError, TypeError, OSError):
        invalid("state_dir must be an existing dedicated directory outside the source tree.")
    controllers = validate_controllers(config.get("controllers", {}), config)
    monitoring = validate_monitoring(config.get("monitoring", {}))
    python_builder = validate_python_builder(config.get("python_builder", {}), controllers)
    return {**config, "instance_id": instance, "public_origin": "https://" + authority,
            "allowed_host": host, "state_dir": root, "controllers": controllers, "monitoring": monitoring,
            "python_builder": python_builder}


def validate_python_builder(value, controllers):
    """Operator declarations, not proof that isolation/scanning is installed.

    No environment fallback or Docker side effects during config loading. The
    existing worker still verifies the local immutable profile and enforces its
    sandbox; deployment still runs the configured digest admission verifier.
    """
    default = {"enabled": False, "base_image": "", "isolation_ready": False, "dependency_network": "none"}
    if isinstance(value, dict) and not value:
        return default
    if not isinstance(value, dict) or set(value) != set(default):
        invalid("Provide exactly enabled, base_image, isolation_ready and dependency_network for python_builder.")
    if type(value["enabled"]) is not bool or type(value["isolation_ready"]) is not bool:
        invalid("Python builder enabled and isolation_ready must be explicit booleans.")
    # Resolve/pull a reviewed profile before configuration; mutable tags and
    # registry credentials are never accepted by this installation contract.
    if not isinstance(value["base_image"], str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", value["base_image"]):
        invalid("Python builder base_image must be an immutable local sha256 image ID.")
    network = value["dependency_network"]
    if (not isinstance(network, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", network)
            or network in {"host", "bridge", "default"}):
        invalid("Python dependencies require none or a dedicated operator-managed network; default and host networks are forbidden.")
    if value["enabled"] and (not value["isolation_ready"]
            or not controllers.get("agent", {}).get("egress_policy_ready")):
        invalid("Enable Python builds only after dedicated builder isolation and Agent controller admission/egress provisioning.")
    return dict(value)


def validate_monitoring(value):
    from django.core.validators import validate_email
    from django.core.exceptions import ValidationError
    if not isinstance(value, dict) or set(value) - {"smtp", "retention_enabled", "retention_days"}:
        invalid("Use only the documented monitoring fields.")
    enabled, days = value.get("retention_enabled", False), value.get("retention_days", 90)
    if type(enabled) is not bool or type(days) is not int or not 30 <= days <= 3650:
        invalid("Monitoring retention requires a boolean and 30–3650 days.")
    smtp = value.get("smtp")
    if smtp is not None:
        if not isinstance(smtp, dict) or set(smtp) != {"host", "port", "username", "password", "tls", "from_email"}:
            invalid("Provide exactly the documented SMTP connection fields.")
        if (not all(isinstance(smtp[key], str) for key in ("host", "username", "password", "tls", "from_email"))
                or not re.fullmatch(r"[A-Za-z0-9.-]+|[a-fA-F0-9:]+", smtp["host"])
                or len(smtp["host"]) > 253 or len(smtp["username"]) > 256 or len(smtp["password"]) > 4096
                or bool(smtp["username"]) != bool(smtp["password"]) or "\x00" in smtp["password"]
                or type(smtp["port"]) is not int or not 1 <= smtp["port"] <= 65535
                or smtp["tls"] not in {"starttls", "tls", "none"}
                or (smtp["tls"] == "none" and smtp["host"] not in {"localhost", "127.0.0.1", "::1"})):
            invalid("Use valid SMTP fields; non-loopback SMTP requires TLS.")
        try:
            validate_email(smtp["from_email"])
        except ValidationError:
            invalid("Use a valid SMTP sender email address.")
        smtp = dict(smtp)
    return {"smtp": smtp, "retention_enabled": enabled, "retention_days": days}


def validate_controllers(value, host):
    """Optional local services; tokens live only in the protected host file.

    Ports are explicit installation assignments, never a Cloud fallback or an
    arbitrary network endpoint. Missing services remain unconfigured.
    """
    if not isinstance(value, dict) or set(value) - {"agent", "provider"}:
        invalid("controllers may configure only agent and provider services.")
    tokens, ports, result = {host["secret_key"], host["encryption_key"]}, set(), {}
    for kind, fields in value.items():
        required = {"port", "token"}
        if kind == "agent":
            required |= {"image_admission_command", "egress_policy_ready"}
        else:
            required.add("release_dir")
        optional = {'network_policy'} if kind == 'agent' else set()
        if not isinstance(fields, dict) or not required <= set(fields) or set(fields) - required - optional:
            invalid("Provide exactly the documented controller fields.")
        token, port = fields["token"], fields["port"]
        if (not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", token)
                or len(set(token)) < 8 or token in tokens):
            invalid("Controller tokens must be strong, independently generated and distinct.")
        if type(port) is not int or not 1024 <= port <= 65535 or port in ports:
            invalid("Controllers require distinct non-privileged loopback ports.")
        tokens.add(token)
        ports.add(port)
        entry = {"token": token, "socket": f"tcp://127.0.0.1:{port}"}
        if kind == "agent":
            command = fields["image_admission_command"]
            if (not isinstance(command, list) or not 1 <= len(command) <= 32
                    or not all(isinstance(arg, str) and arg and len(arg) <= 2048
                               and not any(char in arg for char in ('\x00', '\n', '\r')) for arg in command)):
                invalid("Image admission must be a bounded executable argument list, not shell text.")
            try:
                executable = Path(command[0])
                if not executable.is_absolute() or not executable.is_file() or executable.is_symlink():
                    raise ValueError()
                storage = (Path(host['state_dir']) / 'storage').resolve()
                if executable.resolve().is_relative_to(storage):
                    raise ValueError()
            except (OSError, ValueError):
                invalid("Image admission requires an absolute regular executable outside workload storage.")
            if type(fields["egress_policy_ready"]) is not bool:
                invalid("egress_policy_ready must be an explicit operator verification boolean.")
            entry.update(image_admission_command=list(command), egress_policy_ready=fields["egress_policy_ready"])
            if 'network_policy' in fields:
                policy = fields['network_policy']
                try:
                    from .docker_network_policy import PersonalNetworkPolicy
                    PersonalNetworkPolicy(host_id=host['instance_id'], configuration=policy)
                    if policy.get('mode') != 'internal':
                        executable = Path(policy['nft_executable'])
                        if (not executable.is_absolute() or not executable.is_file() or executable.is_symlink()
                                or executable.resolve().is_relative_to(storage)):
                            raise ValueError()
                except (OSError, ValueError, TypeError, KeyError):
                    invalid('Network policy requires internal isolation or a protected nftables executable with exact approved endpoints.')
                entry['network_policy'] = dict(policy)
        else:
            try:
                directory = Path(fields['release_dir'])
                if not directory.is_absolute() or not directory.is_dir() or directory.is_symlink():
                    raise ValueError()
                directory = directory.resolve(strict=True)
                storage = (Path(host['state_dir']) / 'storage').resolve()
                if directory.is_relative_to(storage) or storage.is_relative_to(directory):
                    raise ValueError()
            except (OSError, ValueError, TypeError):
                invalid("Provider release_dir must be a dedicated absolute directory separate from workload storage.")
            entry['release_dir'] = str(directory)
        result[kind] = entry
    return result


def _windows_private_acl(path):
    # Only the current user, administrators and SYSTEM may read the config.
    # The path is passed as data, never interpolated into a shell program.
    script = r"""
$ErrorActionPreference = 'Stop'
$allowed = @([System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value, 'S-1-5-18', 'S-1-5-32-544')
$acl = [System.IO.File]::GetAccessControl($env:NEXUS_PERSONAL_ACL_TARGET)
$rules = $acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])
if ($rules.Count -eq 0) { exit 1 }
foreach ($rule in $rules) {
  if ($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -notin $allowed) { exit 1 }
}
"""
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            env={**os.environ, "NEXUS_PERSONAL_ACL_TARGET": str(path)}, capture_output=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        invalid("Could not verify configuration file permissions.")
    if result.returncode:
        invalid("Configuration ACL must restrict access to the current user, SYSTEM and administrators.")


def read_protected_json(value):
    """Read a local protected input without logging or selecting a host."""
    if not value:
        invalid("Set NEXUS_PERSONAL_CONFIG to a protected configuration file.")
    path = Path(value)
    try:
        if not path.is_absolute() or path.is_symlink():
            invalid("Use an absolute regular configuration file, not a symlink.")
        with path.open("rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 32768:
                invalid("Configuration must be a regular file of at most 32 KiB.")
            if os.name == "nt":
                _windows_private_acl(path)
            elif info.st_uid != os.getuid() or info.st_mode & 0o077:
                invalid("Configuration must belong to the current user with mode 0600 or stricter.")
            raw = stream.read(32769)
            if len(raw) > 32768:
                invalid("Configuration exceeds 32 KiB.")
        config = json.loads(raw)
    except (OSError, ValueError, UnicodeError):
        invalid("Cannot read a valid protected configuration; contents are never logged.")
    return config


def load_config(environ):
    return validate_config(read_protected_json(environ.get("NEXUS_PERSONAL_CONFIG", "")))
