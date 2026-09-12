"""Explicit Personal worker firewall contract, not a Docker daemon substitute."""
import hashlib
import re
import subprocess
import sys
from .egress_policy import render_policy


BRIDGE_OPTION = 'com.docker.network.bridge.name'


class PersonalNetworkPolicy:
    def __init__(self, *, host_id, configuration, controller_container=''):
        if not isinstance(host_id, str) or not host_id or host_id == 'local' or len(host_id) > 128:
            raise ValueError('EGRESS_HOST_INVALID')
        if controller_container:
            raise ValueError('EGRESS_CONTROLLER_TOPOLOGY_UNSUPPORTED')
        if not isinstance(configuration, dict):
            raise ValueError('EGRESS_CONFIGURATION_INVALID')
        self.host_id = host_id
        identity = hashlib.sha256(host_id.encode()).hexdigest()
        self.prefix = 'nx' + identity[:6]
        self.table = 'nexus_agent_egress_' + identity[:16]
        self.mode = configuration.get('mode', 'nftables')
        if self.mode == 'internal':
            if set(configuration) != {'mode'}:
                raise ValueError('EGRESS_CONFIGURATION_INVALID')
            self.executable = ''
            self.rules = ''
            return
        if self.mode != 'nftables' or set(configuration) != {'nft_executable', 'tcp_endpoints', 'dns_servers'}:
            raise ValueError('EGRESS_CONFIGURATION_INVALID')
        self.executable = configuration['nft_executable']
        self.rules = render_policy({'schema_version': 1, 'interfaces': [self.prefix + '*'],
            'tcp_endpoints': configuration['tcp_endpoints'], 'dns_servers': configuration['dns_servers']}, table=self.table)

    def bridge_name(self, network):
        if not isinstance(network, str) or not re.fullmatch(r'nexus-agent-[A-Za-z0-9]+-[A-Za-z0-9]+-net', network):
            raise ValueError('EGRESS_NETWORK_IDENTITY_INVALID')
        return self.prefix + hashlib.sha256(network.encode()).hexdigest()[:7]

    def _owner(self, labels):
        if (not isinstance(labels, dict) or labels.get('nexus.managed') != 'agent'
                or labels.get('nexus.agent.host') != self.host_id or not labels.get('nexus.agent.runtime')):
            raise ValueError('EGRESS_NETWORK_OWNER_INVALID')

    def _identity(self, network, labels):
        self._owner(labels)
        runtime = labels['nexus.agent.runtime']
        if (not isinstance(runtime, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,64}', runtime)
                or not network.startswith('nexus-agent-' + runtime.replace('-', '') + '-')):
            raise ValueError('EGRESS_NETWORK_OWNER_INVALID')

    def apply(self):
        if self.mode == 'internal':
            return
        if sys.platform != 'linux':
            raise RuntimeError('EGRESS_NFTABLES_LINUX_REQUIRED')
        # The bounded input is an atomic replacement of this instance's table.
        # Privileges/executable are operator-provisioned; never invoke sudo,
        # switch namespaces, modify sysctls or grant capabilities implicitly.
        try:
            result = subprocess.run([self.executable, '-f', '-'], input=self.rules.encode(),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        except (OSError, ValueError, subprocess.SubprocessError):
            raise RuntimeError('EGRESS_POLICY_APPLY_FAILED') from None
        if result.returncode:
            raise RuntimeError('EGRESS_POLICY_APPLY_FAILED')

    def prepare(self, network, labels):
        self._identity(network, labels)
        if self.mode == 'internal':
            return ['--internal']
        bridge = self.bridge_name(network)
        # Wildcard coverage exists before Docker creates the interface, not
        # after attaching the first untrusted process to an unprotected bridge.
        self.apply()
        return ['--opt', BRIDGE_OPTION + '=' + bridge]

    def validate_network(self, info, network, labels):
        self._identity(network, labels)
        if self.mode == 'internal':
            if (not isinstance(info, dict) or info.get('Driver') != 'bridge' or info.get('Name') != network
                    or info.get('Internal') is not True or not isinstance(info.get('Labels'), dict)
                    or any((info.get('Labels') or {}).get(key) != value for key, value in labels.items())):
                raise RuntimeError('EGRESS_NETWORK_REDEPLOY_REQUIRED')
            return
        if (not isinstance(info, dict) or info.get('Driver') != 'bridge' or info.get('Name') != network
                or not isinstance(info.get('Options'), dict) or not isinstance(info.get('Labels'), dict)
                or (info.get('Options') or {}).get(BRIDGE_OPTION) != self.bridge_name(network)
                or any((info.get('Labels') or {}).get(key) != value for key, value in labels.items())):
            raise RuntimeError('EGRESS_NETWORK_REDEPLOY_REQUIRED')

    def validate_container(self, info, network):
        host = info.get('HostConfig') if isinstance(info, dict) else None
        restart = host.get('RestartPolicy') if isinstance(host, dict) else None
        if not isinstance(restart, dict) or restart.get('Name') != 'no':
            raise RuntimeError('EGRESS_RESTART_POLICY_REDEPLOY_REQUIRED')
        settings = info.get('NetworkSettings')
        networks = settings.get('Networks') if isinstance(settings, dict) else None
        if host.get('NetworkMode') != network or not isinstance(networks, dict) or set(networks) != {network}:
            raise RuntimeError('EGRESS_CONTAINER_NETWORK_REDEPLOY_REQUIRED')


def from_settings(settings):
    return PersonalNetworkPolicy(host_id=settings.NEXUS_AGENT_RUNTIME_HOST_ID,
        configuration=settings.NEXUS_PERSONAL_NETWORK_POLICY,
        controller_container=getattr(settings, 'NEXUS_AGENT_RUNTIME_CONTROLLER_CONTAINER', ''))
