"""Built-in topology templates (plan.md section 7.2).

Two templates are provided, both deliberately *non-redundant* (no parallel
links between the same pair of routers): a link failure therefore really does
remove the path, which lets the traceroute probe localize a fault to a specific
link. Every topology also contains a *distinct service route* — the resolver sits
on a different segment than the application servers, so a DNS flow and an
application flow traverse different links. The trade-off (no failover) is
documented in docs/limitations.md.
"""

from __future__ import annotations

from .graph import (
    DnsRecord,
    Link,
    Node,
    NodeType,
    ServiceSpec,
    Topology,
    validate_topology,
)

# ---------------------------------------------------------------------------
# Template A — Small Campus Network
# ---------------------------------------------------------------------------

_CAMPUS = Topology(
    id="campus-basic",
    name="Small Campus Network",
    description=(
        "Student clients behind an access router, a campus router, and a server "
        "segment with a web server and a database. The DNS resolver sits on the "
        "client segment, so name resolution and the web flow use different paths."
    ),
    nodes=[
        Node(
            id="client-1",
            name="Student Client 1",
            type=NodeType.HOST,
            ip_address="10.10.0.10",
            gateway="access-rtr",
            position={"x": 40, "y": 180},
            description="Primary diagnostic source host.",
        ),
        Node(
            id="client-2",
            name="Student Client 2",
            type=NodeType.HOST,
            ip_address="10.10.0.11",
            gateway="access-rtr",
            position={"x": 40, "y": 300},
            description="Second host on the same access segment.",
        ),
        Node(
            id="ops-1",
            name="Lab Ops Workstation",
            type=NodeType.HOST,
            ip_address="10.10.0.12",
            gateway="access-rtr",
            position={"x": 40, "y": 420},
            description="Administrative host used for cross-checks.",
        ),
        Node(
            id="dns-1",
            name="Campus DNS Resolver",
            type=NodeType.DNS_SERVER,
            ip_address="10.10.0.53",
            position={"x": 280, "y": 60},
            dns_records=[
                DnsRecord(name="web.campus.test", address="10.30.0.80"),
                DnsRecord(name="db.campus.test", address="10.30.0.81"),
            ],
            description="Authoritative lab resolver for the campus zone.",
        ),
        Node(
            id="access-rtr",
            name="Access Router",
            type=NodeType.ROUTER,
            ip_address="10.10.0.1",
            position={"x": 280, "y": 240},
            description="Default gateway for the 10.10.0.0/24 student segment.",
        ),
        Node(
            id="campus-rtr",
            name="Campus Router",
            type=NodeType.ROUTER,
            ip_address="10.20.0.1",
            position={"x": 520, "y": 240},
            description="Core campus router between the access and server segments.",
        ),
        Node(
            id="edge-rtr",
            name="Server Segment Router",
            type=NodeType.ROUTER,
            ip_address="10.30.0.1",
            position={"x": 760, "y": 240},
            description="Router for the 10.30.0.0/24 server segment.",
        ),
        Node(
            id="web-1",
            name="Campus Web Server",
            type=NodeType.APP_SERVER,
            ip_address="10.30.0.80",
            position={"x": 1000, "y": 160},
            services=[
                ServiceSpec(
                    name="web",
                    port=80,
                    description="HTTP service (simulated).",
                    response_bytes=512,
                ),
                ServiceSpec(
                    name="https",
                    port=443,
                    description="HTTPS service (simulated).",
                    response_bytes=900,
                ),
            ],
            dns_records=[DnsRecord(name="web.campus.test", address="10.30.0.80")],
            description="Hosts the campus web application.",
        ),
        Node(
            id="db-1",
            name="Campus Database",
            type=NodeType.APP_SERVER,
            ip_address="10.30.0.81",
            position={"x": 1000, "y": 340},
            services=[
                ServiceSpec(
                    name="postgres",
                    port=5432,
                    description="Database service (simulated).",
                    response_bytes=256,
                )
            ],
            dns_records=[DnsRecord(name="db.campus.test", address="10.30.0.81")],
            description="Backend database for the web application.",
        ),
    ],
    links=[
        Link(id="l-client1-access", node_a="client-1", node_b="access-rtr",
             latency_ms=1.0, mtu_bytes=1500, bandwidth_mbps=1000.0,
             description="Access link of Student Client 1 (default gateway link)."),
        Link(id="l-client2-access", node_a="client-2", node_b="access-rtr",
             latency_ms=1.0, mtu_bytes=1500, bandwidth_mbps=1000.0),
        Link(id="l-ops-access", node_a="ops-1", node_b="access-rtr",
             latency_ms=1.1, mtu_bytes=1500, bandwidth_mbps=1000.0),
        Link(id="l-access-dns", node_a="access-rtr", node_b="dns-1",
             latency_ms=1.5, mtu_bytes=1500, bandwidth_mbps=1000.0,
             description="Resolver sits on the access segment (distinct service route)."),
        Link(id="l-access-campus", node_a="access-rtr", node_b="campus-rtr",
             latency_ms=2.5, mtu_bytes=1500, bandwidth_mbps=100.0,
             description="Campus uplink."),
        Link(id="l-campus-edge", node_a="campus-rtr", node_b="edge-rtr",
             latency_ms=3.0, mtu_bytes=1500, bandwidth_mbps=100.0,
             description="Server-segment uplink."),
        Link(id="l-edge-web", node_a="edge-rtr", node_b="web-1",
             latency_ms=0.8, mtu_bytes=1500, bandwidth_mbps=1000.0),
        Link(id="l-edge-db", node_a="edge-rtr", node_b="db-1",
             latency_ms=0.9, mtu_bytes=1500, bandwidth_mbps=1000.0),
    ],
)

# ---------------------------------------------------------------------------
# Template B — Multi-Hop Network
# ---------------------------------------------------------------------------

_MULTIHOP = Topology(
    id="multihop-wan",
    name="Multi-Hop Branch Network",
    description=(
        "Client segment, edge router, core router, and branch router ahead of the "
        "application and database servers. Four-layer-3-hop paths give traceroute "
        "enough hops to localize a fault to a specific link. The resolver is "
        "attached to the edge router, on its own segment."
    ),
    nodes=[
        Node(
            id="client-1",
            name="Branch Client",
            type=NodeType.HOST,
            ip_address="10.100.0.10",
            gateway="edge-rtr",
            position={"x": 40, "y": 220},
            description="Diagnostic source host on the branch LAN.",
        ),
        Node(
            id="client-2",
            name="Branch Client 2",
            type=NodeType.HOST,
            ip_address="10.100.0.11",
            gateway="edge-rtr",
            position={"x": 40, "y": 340},
        ),
        Node(
            id="dns-1",
            name="Edge DNS Resolver",
            type=NodeType.DNS_SERVER,
            ip_address="10.100.0.53",
            position={"x": 300, "y": 60},
            dns_records=[
                DnsRecord(name="api.multi.test", address="10.40.0.80"),
                DnsRecord(name="db.multi.test", address="10.40.0.81"),
            ],
            description="Resolver local to the branch client segment.",
        ),
        Node(
            id="edge-rtr",
            name="Edge Router",
            type=NodeType.ROUTER,
            ip_address="10.100.0.1",
            position={"x": 300, "y": 240},
            description="Default gateway of the 10.100.0.0/24 segment.",
        ),
        Node(
            id="core-rtr",
            name="Core Router",
            type=NodeType.ROUTER,
            ip_address="10.200.0.1",
            position={"x": 560, "y": 240},
            description="Transit router between edge and branch.",
        ),
        Node(
            id="branch-rtr",
            name="Branch Router",
            type=NodeType.ROUTER,
            ip_address="10.30.0.1",
            position={"x": 820, "y": 240},
            description="Router in front of the data-centre segment.",
        ),
        Node(
            id="app-1",
            name="API Server",
            type=NodeType.APP_SERVER,
            ip_address="10.40.0.80",
            position={"x": 1060, "y": 160},
            services=[
                ServiceSpec(
                    name="api",
                    port=8080,
                    description="HTTP API (simulated).",
                    response_bytes=512,
                ),
                ServiceSpec(
                    name="admin",
                    port=9090,
                    description="Administrative interface (simulated).",
                    response_bytes=1200,
                ),
            ],
            dns_records=[DnsRecord(name="api.multi.test", address="10.40.0.80")],
            description="Application server behind three router hops.",
        ),
        Node(
            id="db-1",
            name="Database Server",
            type=NodeType.APP_SERVER,
            ip_address="10.40.0.81",
            position={"x": 1060, "y": 340},
            services=[
                ServiceSpec(
                    name="postgres",
                    port=5432,
                    description="Database service (simulated).",
                    response_bytes=256,
                )
            ],
            dns_records=[DnsRecord(name="db.multi.test", address="10.40.0.81")],
            description="Database in the same segment as the API server.",
        ),
    ],
    links=[
        Link(id="l-client1-edge", node_a="client-1", node_b="edge-rtr",
             latency_ms=1.0, mtu_bytes=1500, bandwidth_mbps=1000.0,
             description="Branch LAN access link (default gateway link)."),
        Link(id="l-client2-edge", node_a="client-2", node_b="edge-rtr",
             latency_ms=1.0, mtu_bytes=1500, bandwidth_mbps=1000.0),
        Link(id="l-edge-dns", node_a="edge-rtr", node_b="dns-1",
             latency_ms=1.2, mtu_bytes=1500, bandwidth_mbps=1000.0,
             description="Resolver segment attached to the edge router."),
        Link(id="l-edge-core", node_a="edge-rtr", node_b="core-rtr",
             latency_ms=6.0, mtu_bytes=1500, bandwidth_mbps=50.0,
             description="Edge-to-core WAN link."),
        Link(id="l-core-branch", node_a="core-rtr", node_b="branch-rtr",
             latency_ms=8.0, mtu_bytes=1500, bandwidth_mbps=20.0,
             description="Core-to-branch WAN link (longest latency in the lab)."),
        Link(id="l-branch-app", node_a="branch-rtr", node_b="app-1",
             latency_ms=1.5, mtu_bytes=1500, bandwidth_mbps=1000.0),
        Link(id="l-branch-db", node_a="branch-rtr", node_b="db-1",
             latency_ms=1.6, mtu_bytes=1500, bandwidth_mbps=1000.0),
    ],
)

TEMPLATES: dict[str, Topology] = {
    "campus-basic": _CAMPUS,
    "multihop-wan": _MULTIHOP,
}


def list_template_ids() -> list[str]:
    return list(TEMPLATES.keys())


def get_template(template_id: str) -> Topology:
    """Return a fresh, validated deep copy of a template."""
    from ..core.errors import NotFoundError

    if template_id not in TEMPLATES:
        raise NotFoundError(
            f"unknown topology template {template_id!r}; available: "
            + ", ".join(list_template_ids()),
            field="template_id",
        )
    clone = TEMPLATES[template_id].model_copy(deep=True)
    return validate_topology(clone)


def template_summaries() -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for template_id, template in TEMPLATES.items():
        out.append(
            {
                "id": template_id,
                "name": template.name,
                "description": template.description,
                "node_count": len(template.nodes),
                "link_count": len(template.links),
                "hosts": [{"id": n.id, "name": n.name, "ip_address": n.ip_address}
                          for n in template.hosts()],
                "services": [
                    {"node_id": node.id, "node_name": node.name, "ip_address": node.ip_address,
                     "service": svc.name, "port": svc.port}
                    for node, svc in template.services()
                ],
                "resolvers": [
                    {"id": n.id, "name": n.name, "ip_address": n.ip_address}
                    for n in template.resolvers()
                ],
            }
        )
    return out
