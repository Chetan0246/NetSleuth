/**
 * Topology view rendered as SVG.
 *
 * A deterministic SVG renderer is used rather than React Flow: the lab topologies
 * are small (~10 nodes), positions come from the backend template, and a plain SVG
 * keeps the build free of an extra dependency while still supporting selection and
 * fault highlighting. Node types are distinguished by shape *and* a text label, so
 * the diagram does not rely on colour alone.
 */

import type { ActiveFault, Topology, TopologyLink, TopologyNode } from "../lib/api";

const NODE_SIZE: Record<string, { w: number; h: number }> = {
  host: { w: 116, h: 58 },
  router: { w: 116, h: 50 },
  dns_server: { w: 128, h: 58 },
  app_server: { w: 128, h: 64 },
};

const TYPE_LABEL: Record<string, string> = {
  host: "HOST",
  router: "ROUTER",
  dns_server: "DNS",
  app_server: "SERVER",
};

function nodeStyle(type: string): { fill: string; stroke: string } {
  switch (type) {
    case "host":
      return { fill: "#eff6ff", stroke: "#3b82f6" };
    case "router":
      return { fill: "#f5f3ff", stroke: "#8b5cf6" };
    case "dns_server":
      return { fill: "#ecfdf5", stroke: "#10b981" };
    default:
      return { fill: "#fff7ed", stroke: "#f97316" };
  }
}

export interface FaultHighlight {
  /** Link ids the injected fault list targets. */
  links: Set<string>;
  /** Node ids the injected fault list targets. */
  nodes: Set<string>;
  /**
   * Service targets as `node:service_name` — the form `FaultConfig.target_id` uses
   * for service-scoped faults (`app/lab/faults.py::_service_target`). The trailing
   * part is the SERVICE name, not a port.
   */
  services: Set<string>;
}

/** Derive the highlight set from the active fault list. */
export function faultHighlights(faults: ActiveFault[]): FaultHighlight {
  const links = new Set<string>();
  const nodes = new Set<string>();
  const services = new Set<string>();
  for (const fault of faults) {
    if (!fault.is_active) continue;
    if (fault.target_kind === "link") links.add(fault.target_id);
    if (fault.target_kind === "node") nodes.add(fault.target_id);
    if (fault.target_kind === "service") {
      const [nodeId, , port] = fault.target_id.split(/[:]/);
      services.add(fault.target_id);
      if (nodeId) nodes.add(nodeId);
      void port;
    }
  }
  return { links, nodes, services };
}

export function TopologyView({
  topology,
  selectedNodeId,
  suspectedComponentId,
  suspects = { links: new Set<string>(), nodes: new Set<string>(), services: new Set<string>() },
  onSelectNode,
  height = 420,
}: {
  topology: Topology;
  selectedNodeId?: string | null;
  suspectedComponentId?: string | null;
  suspects?: FaultHighlight;
  onSelectNode?: (nodeId: string) => void;
  height?: number;
}) {
  const nodes = topology.nodes;
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const pad = 40;

  const maxX = Math.max(...nodes.map((node) => node.position.x + 140), 400);
  const maxY = Math.max(...nodes.map((node) => node.position.y + 90), 300);

  const linkEndpoints = (link: TopologyLink) => {
    const a = nodeById.get(link.node_a);
    const b = nodeById.get(link.node_b);
    if (!a || !b) return null;
    const sizeA = NODE_SIZE[a.type] ?? NODE_SIZE.host;
    const sizeB = NODE_SIZE[b.type] ?? NODE_SIZE.host;
    return {
      a,
      b,
      ax: a.position.x + sizeA.w / 2,
      ay: a.position.y + sizeA.h / 2,
      bx: b.position.x + sizeB.w / 2,
      by: b.position.y + sizeB.h / 2,
    };
  };

  /**
   * Does the diagnosed component refer to this link?
   *
   * Only link and node components are matched here. A `service` component is the
   * diagnosis saying "the fault is in the application on this host", so it is shown by
   * highlighting that host — which the node branch already handles. An earlier revision
   * also carried a dead `"service"` branch that no caller ever reached.
   */
  const isSuspectedLink = (linkId: string) =>
    suspectedComponentId === linkId;
  const isSuspectedNode = (nodeId: string) =>
    suspectedComponentId === nodeId ||
    (suspectedComponentId?.startsWith(`${nodeId}:`) ?? false);

  return (
    <div className="overflow-auto rounded border border-slate-200 bg-slate-50">
      <svg
        role="img"
        aria-label={`Network topology: ${topology.name}. ${nodes.length} nodes, ${topology.links.length} links.`}
        viewBox={`0 0 ${maxX + pad} ${maxY + pad}`}
        style={{ minWidth: 720, width: "100%", height }}
      >
        <defs>
          <marker
            id="arrow-up"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#475569" />
          </marker>
        </defs>

        {topology.links.map((link) => {
          const ends = linkEndpoints(link);
          if (!ends) return null;
          // Three independent visual channels, so an injected fault and a diagnosed
          // suspicion can never be confused:
          //   red solid  = an injected fault (what the operator set up)
          //   blue dash  = the component the diagnosis suspects (a conclusion)
          //   grey       = a healthy link
          // The legend below states exactly this.
          const faulty = suspects.links.has(link.id);
          const suspect = isSuspectedLink(link.id);
          const stroke = faulty ? "#dc2626" : suspect ? "#0284c7" : "#94a3b8";
          const width = faulty || suspect ? 3 : 1.5;
          const dash = suspect ? "6 4" : link.up ? undefined : "7 5";
          const midX = (ends.ax + ends.bx) / 2;
          const midY = (ends.ay + ends.by) / 2;
          return (
            <g key={link.id}>
              <line
                x1={ends.ax}
                y1={ends.ay}
                x2={ends.bx}
                y2={ends.by}
                stroke={stroke}
                strokeWidth={width}
                strokeDasharray={dash}
              />
              <g transform={`translate(${midX}, ${midY})`}>
                <rect
                  x={-56}
                  y={-11}
                  width={112}
                  height={22}
                  rx={4}
                  fill="#ffffff"
                  stroke={faulty ? "#fecaca" : "#e2e8f0"}
                />
                <text
                  textAnchor="middle"
                  dominantBaseline="middle"
                  fontSize={9}
                  fill="#475569"
                  fontFamily="ui-monospace, monospace"
                >
                  {`${link.up ? "" : "DOWN "}${link.latency_ms}ms mtu${link.mtu_bytes}${
                    link.packet_loss_rate > 0 ? ` loss${(link.packet_loss_rate * 100).toFixed(0)}%` : ""
                  }`}
                </text>
              </g>
            </g>
          );
        })}

        {nodes.map((node) => (
          <TopologyNodeShape
            key={node.id}
            node={node}
            selected={selectedNodeId === node.id}
            faulty={
              suspects.nodes.has(node.id) ||
              Array.from(suspects.services).some((target) =>
                target.startsWith(`${node.id}:`),
              )
            }
            suspected={isSuspectedNode(node.id)}
            onSelect={onSelectNode}
          />
        ))}
      </svg>
      <div className="flex flex-wrap gap-3 border-t border-slate-200 bg-white px-3 py-2 text-[11px] text-slate-600">
        <LegendShape label="HOST" fill="#eff6ff" stroke="#3b82f6" kind="rect" />
        <LegendShape label="ROUTER" fill="#f5f3ff" stroke="#8b5cf6" kind="hex" />
        <LegendShape label="DNS" fill="#ecfdf5" stroke="#10b981" kind="ellipse" />
        <LegendShape label="SERVER" fill="#fff7ed" stroke="#f97316" kind="rect" />
        <span className="inline-flex items-center gap-1">
          <svg width="26" height="8" aria-hidden="true">
            <line x1="0" y1="4" x2="26" y2="4" stroke="#dc2626" strokeWidth="3" />
          </svg>
          injected fault
        </span>
        <span className="inline-flex items-center gap-1">
          <svg width="26" height="8" aria-hidden="true">
            <line
              x1="0"
              y1="4"
              x2="26"
              y2="4"
              stroke="#0284c7"
              strokeWidth="3"
              strokeDasharray="6 4"
            />
          </svg>
          suspected by diagnosis
        </span>
        <span className="inline-flex items-center gap-1">
          <svg width="26" height="8" aria-hidden="true">
            <line x1="0" y1="4" x2="26" y2="4" stroke="#94a3b8" strokeWidth="2" strokeDasharray="7 5" />
          </svg>
          link down
        </span>
      </div>
    </div>
  );
}

function LegendShape({
  label,
  fill,
  stroke,
  kind,
}: {
  label: string;
  fill: string;
  stroke: string;
  kind: "rect" | "hex" | "ellipse";
}) {
  return (
    <span className="inline-flex items-center gap-1">
      <svg width="30" height="16" aria-hidden="true">
        {kind === "ellipse" ? (
          <ellipse cx="15" cy="8" rx="13" ry="7" fill={fill} stroke={stroke} strokeWidth="1.5" />
        ) : kind === "hex" ? (
          <polygon
            points="5,8 10,2 21,2 26,8 21,14 10,14"
            fill={fill}
            stroke={stroke}
            strokeWidth="1.5"
          />
        ) : (
          <rect x="2" y="2" width="26" height="12" rx="3" fill={fill} stroke={stroke} strokeWidth="1.5" />
        )}
      </svg>
      {label}
    </span>
  );
}

function TopologyNodeShape({
  node,
  selected,
  faulty,
  suspected,
  onSelect,
}: {
  node: TopologyNode;
  selected: boolean;
  /** An injected fault targets this node (or a service on it). */
  faulty: boolean;
  /** The diagnosis suspects this node or a service on it. */
  suspected: boolean;
  onSelect?: (nodeId: string) => void;
}) {
  const size = NODE_SIZE[node.type] ?? NODE_SIZE.host;
  const style = nodeStyle(node.type);
  const { x, y } = node.position;
  // Injected fault (red) outranks a diagnosis suspicion (blue), which outranks
  // selection (also blue, but with a dashed ring) — the same precedence the link
  // styling and the legend use, so a red node always means "you injected this".
  const stroke = faulty ? "#dc2626" : suspected ? "#0284c7" : selected ? "#0369a1" : style.stroke;
  const strokeWidth = faulty || suspected ? 2.5 : selected ? 2 : 1.5;
  const strokeDash = suspected && !faulty ? "6 3" : undefined;

  const body =
    node.type === "router" ? (
      <polygon
        points={`${x + 8},${y} ${x + size.w - 8},${y} ${x + size.w},${y + size.h / 2} ${
          x + size.w - 8
        },${y + size.h} ${x + 8},${y + size.h} ${x},${y + size.h / 2}`}
        fill={faulty ? "#fee2e2" : style.fill}
        stroke={stroke}
        strokeWidth={strokeWidth}
        strokeDasharray={strokeDash}
      />
    ) : node.type === "dns_server" ? (
      <ellipse
        cx={x + size.w / 2}
        cy={y + size.h / 2}
        rx={size.w / 2}
        ry={size.h / 2}
        fill={faulty ? "#fee2e2" : style.fill}
        stroke={stroke}
        strokeWidth={strokeWidth}
        strokeDasharray={strokeDash}
      />
    ) : (
      <rect
        x={x}
        y={y}
        width={size.w}
        height={size.h}
        rx={6}
        fill={faulty ? "#fee2e2" : style.fill}
        stroke={stroke}
        strokeWidth={strokeWidth}
        strokeDasharray={strokeDash}
      />
    );

  const services = node.services.map((service) => `${service.name}:${service.port}`).join(" ");

  return (
    <g
      tabIndex={onSelect ? 0 : undefined}
      role={onSelect ? "button" : undefined}
      aria-label={
        onSelect
          ? `${node.name}, ${TYPE_LABEL[node.type]}, IP ${node.ip_address}. Select as source or destination.`
          : undefined
      }
      onClick={onSelect ? () => onSelect(node.id) : undefined}
      onKeyDown={
        onSelect
          ? (event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onSelect(node.id);
              }
            }
          : undefined
      }
      style={{ cursor: onSelect ? "pointer" : "default" }}
    >
      {body}
      <text x={x + 8} y={y + 15} fontSize={10} fontWeight={700} fill="#334155">
        {TYPE_LABEL[node.type]}
        {faulty ? " ⚠" : ""}
      </text>
      <text x={x + 8} y={y + 29} fontSize={11} fill="#0f172a">
        {node.name.length > 18 ? `${node.name.slice(0, 17)}…` : node.name}
      </text>
      <text x={x + 8} y={y + 42} fontSize={10} fill="#475569" fontFamily="ui-monospace, monospace">
        {node.ip_address}
      </text>
      {services && (
        <text x={x + 8} y={y + 55} fontSize={9} fill="#7c2d12">
          {services}
        </text>
      )}
    </g>
  );
}
