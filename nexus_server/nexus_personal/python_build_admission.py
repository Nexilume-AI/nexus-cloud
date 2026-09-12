"""Admit only images produced by this host's fixed Python build recipe."""
import argparse
import json
import re
import subprocess
import sys


DIGEST = re.compile(r"sha256:[a-f0-9]{64}")
HOST = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


def verify(host_id, digest):
    if not HOST.fullmatch(host_id) or not DIGEST.fullmatch(digest):
        return False
    try:
        result = subprocess.run(["docker", "image", "inspect", digest], stdin=subprocess.DEVNULL,
            capture_output=True, timeout=30, check=False)
        rows = json.loads(result.stdout) if result.returncode == 0 and len(result.stdout) <= 1024 * 1024 else []
        info = rows[0] if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict) else {}
        config = info.get("Config") if isinstance(info.get("Config"), dict) else {}
        labels = config.get("Labels") if isinstance(config.get("Labels"), dict) else {}
        return (info.get("Id") == digest and labels.get("nexus.managed") == "python-build"
            and labels.get("nexus.python.host") == host_id
            and re.fullmatch(r"[a-f0-9]{32}", str(labels.get("nexus.python.build", ""))) is not None
            and re.fullmatch(r"[a-f0-9]{64}", str(labels.get("nexus.python.source", ""))) is not None
            and config.get("User") == "65532:65532"
            and config.get("WorkingDir") == "/opt/nexus-python"
            and config.get("Entrypoint") == ["python", "/opt/nexus-python/nexus_boot.py"])
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--host-id", required=True)
    parser.add_argument("digest")
    try:
        options = parser.parse_args(argv)
        accepted = verify(options.host_id, options.digest)
    except SystemExit:
        accepted = False
    if not accepted:
        print("PYTHON_BUILD_IMAGE_REJECTED", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
