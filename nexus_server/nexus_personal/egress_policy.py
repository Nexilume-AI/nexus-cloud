"""Render an explicit worker-owned nftables policy; never install it implicitly.

The operator supplies resolved, approved endpoint addresses. Explicit bridge
lists cover only those interfaces; the optional Personal lifecycle backend uses
a reserved per-worker prefix before bridge creation. This renderer alone must
not set egress_policy_ready.
"""
from __future__ import annotations

import ipaddress
import re


TABLE = "nexus_agent_egress"


def _address(value):
    if not isinstance(value, str) or len(value) > 45 or "%" in value:
        raise ValueError("EGRESS_ADDRESS_INVALID")
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise ValueError("EGRESS_ADDRESS_INVALID") from None
    if (address.is_unspecified or address.is_loopback or address.is_multicast
            or address.is_link_local or getattr(address, "ipv4_mapped", None)):
        raise ValueError("EGRESS_ADDRESS_INVALID")
    return str(address), "ip" if address.version == 4 else "ip6"


def render_policy(policy, *, table=TABLE):
    """Return one atomic batch replacing only our named table, default deny.

    TCP endpoints are exact address/port pairs; approved DNS addresses get only
    TCP/UDP port53. No DNS lookup or arbitrary nft syntax occurs while rendering.
    Established outgoing connections are re-evaluated after policy replacement.
    """
    if not isinstance(table, str) or not re.fullmatch(r"nexus_agent_egress(?:_[a-f0-9]{16})?", table):
        raise ValueError("EGRESS_TABLE_INVALID")
    if (not isinstance(policy, dict) or set(policy) != {
            "schema_version", "interfaces", "tcp_endpoints", "dns_servers"}
            or type(policy["schema_version"]) is not int or policy["schema_version"] != 1):
        raise ValueError("EGRESS_POLICY_INVALID")
    interfaces = policy["interfaces"]
    if (not isinstance(interfaces, list) or not 1 <= len(interfaces) <= 64
            or any(not isinstance(name, str) or not (re.fullmatch(r"[A-Za-z0-9_-]{1,15}", name)
                       or re.fullmatch(r"nx[a-f0-9]{6}\*", name))
                   or name == "lo" for name in interfaces) or len(set(interfaces)) != len(interfaces)):
        raise ValueError("EGRESS_INTERFACES_INVALID")
    endpoints, dns = policy["tcp_endpoints"], policy["dns_servers"]
    if not isinstance(endpoints, list) or len(endpoints) > 256 or not isinstance(dns, list) or len(dns) > 8:
        raise ValueError("EGRESS_ENDPOINTS_INVALID")
    destinations = set()
    for endpoint in endpoints:
        if not isinstance(endpoint, dict) or set(endpoint) != {"address", "port"}:
            raise ValueError("EGRESS_ENDPOINT_INVALID")
        port = endpoint["port"]
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("EGRESS_PORT_INVALID")
        address, family = _address(endpoint["address"])
        destinations.add((family, address, port))
    resolvers = {_address(value) for value in dns}
    selected = "{ " + ", ".join('"' + name + '"' for name in sorted(interfaces)) + " }"
    peers = '{ "nx*" }' if any(name.endswith('*') for name in interfaces) else selected
    prefix = f"inet {table}"
    lines = [f"add table {prefix}", f"flush table {prefix}",
        f"add chain {prefix} endpoints",
        f"add rule {prefix} endpoints ct state invalid counter drop",
    ]
    for family, address, port in sorted(destinations):
        lines.append(f"add rule {prefix} endpoints {family} daddr {address} tcp dport {port} counter accept")
    for address, family in sorted(resolvers):
        lines.append(f"add rule {prefix} endpoints {family} daddr {address} meta l4proto {{ tcp, udp }} th dport 53 counter accept")
    lines += [
        f'add rule {prefix} endpoints counter reject with icmpx type admin-prohibited comment "unapproved-egress"',
        f"add chain {prefix} forward {{ type filter hook forward priority -10; policy accept; }}",
        # Peer denial precedes endpoint permits, even when a peer IP was listed.
        f'add rule {prefix} forward iifname {selected} oifname {peers} counter drop comment "agent-peer-denied"',
        f"add rule {prefix} forward iifname {selected} jump endpoints",
        f"add rule {prefix} forward oifname {selected} ct state established,related counter accept",
        f'add rule {prefix} forward oifname {selected} counter drop comment "unsolicited-ingress"',
        f"add chain {prefix} input {{ type filter hook input priority -10; policy accept; }}",
        # IPv6 neighbor resolution is necessary before any routed TCP packet.
        f"add rule {prefix} input iifname {selected} ip6 hoplimit 255 icmpv6 type {{ nd-neighbor-solicit, nd-neighbor-advert }} counter accept",
        # Only replies to host-initiated MCP traffic bypass endpoint checks.
        # Agent-initiated connections to host services still recheck each packet.
        f"add rule {prefix} input iifname {selected} ct direction reply ct state established,related counter accept",
        f"add rule {prefix} input iifname {selected} jump endpoints",
    ]
    return "\n".join(lines) + "\n"
