"""Operator-owned local profile preparation. Does not restart services or touch a DB."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument("--image", default="nexus-agent-python:py312-v1")
parser.add_argument("--use-existing", action="store_true")
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
cli = shutil.which("docker") or r"D:\Docker\Desktop\resources\bin\docker.exe"
if not args.use_existing:
    subprocess.run([cli, "build", "-f", str(root / "nexus_server/apps/agents/python_profile/Dockerfile"), "-t", args.image, str(root)], check=True)
image = json.loads(subprocess.check_output([cli, "image", "inspect", args.image]))[0]
digest = image["Id"]
if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
    raise SystemExit("Profile did not resolve to an immutable image ID.")
# Trusted operator profile only: never import uploaded code or contact a Router.
# Verify the native adapter too. Old images can import FastMCP successfully but
# still cannot upload NexusAgent source. Failure leaves the old profile untouched.
subprocess.run([cli, "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "65532:65532",
    "--entrypoint", "python", digest, "-c", """
import sys
from importlib.metadata import version
assert sys.version_info[:2] == (3, 12)
from fastmcp import FastMCP
from nexus_agent import NexusAgent, McpToolDescriptor
from nexus_agent.fastmcp import NexusMCPServer
assert hasattr(NexusAgent, 'as_mcp_server'), 'Nexus SDK 0.46.0 or newer is required'
from nexus_agent.hosted_trust import hosted_cloud_opener
assert callable(hosted_cloud_opener), 'Hosted Cloud trust support is required'
agent = NexusAgent(runtime='hosted', cloud_publish=True)
agent.capability('profile_probe', tool=McpToolDescriptor(name='profile_probe'))(lambda payload: {'ok': True})
assert isinstance(agent.as_mcp_server(), NexusMCPServer)
print('Verified Nexus SDK ' + version('nexus-agent-sdk') + ' with native MCP adapter')
"""], check=True)
target = root / ".local/nexus-cloud/python-profile.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps({"image": args.image, "digest": digest, "profile": "python312-nexus-v1"}, indent=2), encoding="utf-8")
print("Python profile prepared. The local Cloud launcher will enable its build worker on the next authorized restart.")
