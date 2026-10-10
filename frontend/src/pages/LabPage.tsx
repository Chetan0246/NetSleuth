/**
 * Page 2 — Network Lab.
 *
 * Template selection, topology view, source/destination selection, fault injection
 * and reset. The fault form is built from the backend's `available_faults` list, so
 * every option offered is one the injector has already validated for this
 * topology — a button here cannot produce a 422.
 */

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, RotateCcw, Trash2 } from "lucide-react";

import {
  api,
  ApiError,
  type ActiveFault,
  type AvailableFault,
  type LabSession,
} from "../lib/api";
import { formatTime, useAsync } from "../lib/useAsync";
import { useSessionContext } from "../App";
import { TopologyView, faultHighlights } from "../components/TopologyView";
import {
  Card,
  EmptyState,
  ErrorNotice,
  Loading,
  ModeBadge,
} from "../components/ui";

/**
 * Stable identity for one injectable fault option.
 *
 * ``fault_type|target_id`` is not unique: a route black hole is offered once per
 * destination server on the same link, so those entries share both fields and the
 * first match would be injected whichever option the user picked. The parameters
 * (which carry the destination) are part of the identity.
 */
function faultOptionKey(item: AvailableFault): string {
  return `${item.fault_type}|${item.target_id}|${JSON.stringify(item.parameters ?? {})}`;
}

export default function LabPage() {
  const { session, sessionId, setSession, refreshSession } = useSessionContext();
  const templates = useAsync(() => api.templates(), []);
  const sessions = useAsync(() => api.listSessions(20), []);

  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [source, setSource] = useState<string>("");
  const [destination, setDestination] = useState<string>("");
  const [service, setService] = useState<string>("");
  const [faultChoice, setFaultChoice] = useState<string>("");
  const [confirmReset, setConfirmReset] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  // Re-default the source/destination/service when a *different* session is loaded.
  // The effect is keyed on the session id only: keying on the whole `session` object
  // re-ran it on every fault mutation (inject/toggle/remove/reset all call
  // `setSession`), silently clearing the user's current selections.
  useEffect(() => {
    if (!session) return;
    const hosts = session.topology.nodes.filter((node) => node.type === "host");
    const servers = session.topology.nodes.filter((node) => node.services.length > 0);
    setSource(hosts[0]?.id ?? "");
    setDestination(servers[0]?.id ?? "");
    setService(servers[0]?.services[0]?.name ?? "");
    setFaultChoice("");
    setSelectedNodeId(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.id]);

  const availableByType = useMemo<
    { type: string; items: AvailableFault[] }[]
  >(() => {
    if (!session) return [];
    const grouped = new Map<string, AvailableFault[]>();
    for (const item of session.available_faults) {
      const list = grouped.get(item.fault_type) ?? [];
      list.push(item);
      grouped.set(item.fault_type, list);
    }
    return Array.from(grouped.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([type, items]) => ({ type, items }));
  }, [session]);

  const highlights = useMemo(
    () => faultHighlights(session?.active_faults ?? []),
    [session],
  );

  const selectedNode = session?.topology.nodes.find((node) => node.id === selectedNodeId);

  async function withBusy<T>(action: () => Promise<T>, successMessage?: string) {
    setBusy(true);
    setActionError(null);
    setNotice(null);
    try {
      const result = await action();
      if (successMessage) setNotice(successMessage);
      return result;
    } catch (cause) {
      setActionError(
        cause instanceof ApiError ? cause : new ApiError(0, "unknown_error", String(cause)),
      );
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function createSession(templateId: string) {
    const created = await withBusy(
      () => api.createSession(templateId),
      `Created a simulated lab session from ${templateId}.`,
    );
    if (created) {
      setSession(created);
      sessions.reload();
    }
  }

  async function loadSession(id: string) {
    const loaded = await withBusy(() => api.getSession(id), `Loaded session ${id}.`);
    if (loaded) setSession(loaded);
  }

  async function injectFault() {
    if (!sessionId || !faultChoice) return;
    const choice = session?.available_faults.find(
      (item) => faultOptionKey(item) === faultChoice,
    );
    if (!choice) return;
    const updated: LabSession | null = await withBusy(
      () =>
        api.applyFault(sessionId, {
          fault_type: choice.fault_type,
          target_id: choice.target_id,
          parameters: choice.parameters,
        }),
      `Injected ${choice.fault_type} on ${choice.target_label}. It is reversible and takes effect on the next probe.`,
    );
    if (updated) {
      setSession(updated);
      sessions.reload();
    }
  }

  async function toggleFault(fault: ActiveFault) {
    if (!sessionId) return;
    const updated = await withBusy(() =>
      api.toggleFault(sessionId, fault.id, !fault.is_active),
    );
    if (updated) {
      setSession(updated);
      // The saved-session list carries `active_fault_count`, so it goes stale otherwise.
      sessions.reload();
    }
  }

  async function removeFault(fault: ActiveFault) {
    if (!sessionId) return;
    const updated = await withBusy(
      () => api.removeFault(sessionId, fault.id),
      `Removed ${fault.fault_type}. The lab is restored to its pristine state.`,
    );
    if (updated) {
      setSession(updated);
      sessions.reload();
    }
  }

  async function resetLab() {
    if (!sessionId) return;
    const updated = await withBusy(
      () => api.resetSession(sessionId),
      "Lab reset: all faults removed and the topology restored.",
    );
    if (updated) setSession(updated);
    setConfirmReset(false);
  }

  async function deleteSession() {
    if (!sessionId) return;
    await withBusy(
      async () => {
        await api.deleteSession(sessionId);
        setSession(null);
        sessions.reload();
      },
      "Session deleted.",
    );
    setConfirmDelete(false);
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold">Network Lab</h2>
          <p className="text-xs text-slate-600">
            Choose a topology template, inspect the simulated links, and inject controlled,
            reversible faults. Link state, routing, DNS, TCP, MTU and service health are all
            modelled by the virtual lab.
          </p>
        </div>
        <ModeBadge mode="SIMULATED LAB" />
      </div>

      {actionError && (
        <ErrorNotice error={actionError} onRetry={() => setActionError(null)} />
      )}
      {notice && (
        <p
          role="status"
          className="rounded border border-green-300 bg-green-50 px-3 py-2 text-sm text-green-900"
        >
          {notice}
        </p>
      )}

      {!session && (
        <Card title="Start a lab session" subtitle="Two topology templates are provided.">
          {templates.loading ? (
            <Loading label="Loading templates…" />
          ) : templates.error ? (
            <ErrorNotice error={templates.error} onRetry={templates.reload} />
          ) : (
            <div className="grid gap-3 md:grid-cols-2">
              {templates.data?.templates.map((template) => (
                <div
                  key={template.id}
                  className="flex flex-col justify-between rounded border border-slate-200 bg-slate-50 p-3"
                >
                  <div>
                    <h3 className="text-sm font-semibold">{template.name}</h3>
                    <p className="mt-1 text-xs text-slate-600">{template.description}</p>
                    <dl className="mt-2 grid grid-cols-2 gap-1 text-xs text-slate-700">
                      <div>
                        <dt className="inline font-medium">nodes: </dt>
                        <dd className="inline">{template.node_count}</dd>
                      </div>
                      <div>
                        <dt className="inline font-medium">links: </dt>
                        <dd className="inline">{template.link_count}</dd>
                      </div>
                      <div className="col-span-2">
                        <dt className="inline font-medium">services: </dt>
                        <dd className="inline">
                          {template.services
                            .map((item) => `${item.service}:${item.port}`)
                            .join(", ") || "none"}
                        </dd>
                      </div>
                      <div className="col-span-2">
                        <dt className="inline font-medium">resolvers: </dt>
                        <dd className="inline">
                          {template.resolvers.map((item) => item.name).join(", ") || "none"}
                        </dd>
                      </div>
                    </dl>
                  </div>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void createSession(template.id)}
                    className="mt-3 rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-60"
                  >
                    Create session from {template.name}
                  </button>
                </div>
              ))}
            </div>
          )}

          <div className="mt-4 border-t border-slate-200 pt-3">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-600">
              Or load a saved session
            </h3>
            {sessions.loading ? (
              <Loading />
            ) : !sessions.data || sessions.data.sessions.length === 0 ? (
              <p className="mt-1 text-sm text-slate-600">No saved sessions yet.</p>
            ) : (
              <ul className="mt-1 divide-y divide-slate-200">
                {sessions.data.sessions.map((item) => (
                  <li key={item.id} className="flex items-center justify-between gap-2 py-1.5">
                    <span className="text-sm">
                      {item.name}{" "}
                      <span className="text-xs text-slate-500">
                        ({item.template_name}, {item.active_fault_count} faults)
                      </span>
                    </span>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void loadSession(item.id)}
                      className="rounded border border-slate-300 bg-white px-2 py-1 text-xs hover:bg-slate-50 disabled:opacity-60"
                    >
                      Load
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Card>
      )}

      {session && (
        <>
          <Card
            title={session.name}
            subtitle={`${session.template_name} · session ${session.id} · seed ${session.random_seed} · created ${formatTime(session.created_at)}`}
            actions={
              <>
                <button
                  type="button"
                  onClick={() => setConfirmReset(true)}
                  disabled={busy || session.active_faults.length === 0}
                  className="inline-flex items-center gap-1 rounded border border-slate-300 bg-white px-2.5 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-50"
                >
                  <RotateCcw size={14} aria-hidden="true" />
                  Reset Lab
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmDelete(true)}
                  disabled={busy}
                  className="inline-flex items-center gap-1 rounded border border-red-300 bg-white px-2.5 py-1.5 text-sm text-red-800 hover:bg-red-50 disabled:opacity-50"
                >
                  <Trash2 size={14} aria-hidden="true" />
                  Delete session
                </button>
              </>
            }
          >
            <TopologyView
              topology={session.topology}
              selectedNodeId={selectedNodeId}
              suspects={highlights}
              onSelectNode={(nodeId) =>
                setSelectedNodeId((current) => (current === nodeId ? null : nodeId))
              }
            />

            {confirmReset && (
              <div
                role="alertdialog"
                aria-label="Confirm lab reset"
                className="mt-3 rounded border border-amber-300 bg-amber-50 p-3 text-sm"
              >
                <p className="flex items-start gap-2 font-medium text-amber-900">
                  <AlertTriangle size={16} aria-hidden="true" className="mt-0.5" />
                  Reset the lab? All {session.active_faults.length} injected fault(s) will be
                  removed and the topology restored to its pristine state.
                </p>
                <div className="mt-2 flex gap-2">
                  <button
                    type="button"
                    onClick={() => void resetLab()}
                    className="rounded bg-amber-800 px-3 py-1 text-xs font-medium text-white hover:bg-amber-900"
                  >
                    Yes, reset the lab
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirmReset(false)}
                    className="rounded border border-amber-400 bg-white px-3 py-1 text-xs"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {confirmDelete && (
              <div
                role="alertdialog"
                aria-label="Confirm session delete"
                className="mt-3 rounded border border-red-300 bg-red-50 p-3 text-sm"
              >
                <p className="font-medium text-red-900">
                  Delete session <code>{session.id}</code> and all diagnoses stored under it? This
                  cannot be undone.
                </p>
                <div className="mt-2 flex gap-2">
                  <button
                    type="button"
                    onClick={() => void deleteSession()}
                    className="rounded bg-red-700 px-3 py-1 text-xs font-medium text-white hover:bg-red-800"
                  >
                    Yes, delete it
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirmDelete(false)}
                    className="rounded border border-red-400 bg-white px-3 py-1 text-xs"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </Card>

          <div className="grid gap-4 lg:grid-cols-3">
            <Card title="Target selection" subtitle="Source and destination for a diagnosis.">
              <div className="space-y-3">
                <label className="block text-xs font-medium text-slate-700">
                  Source host
                  <select
                    value={source}
                    onChange={(event) => setSource(event.target.value)}
                    className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
                  >
                    {session.topology.nodes
                      .filter((node) => node.type === "host")
                      .map((node) => (
                        <option key={node.id} value={node.id}>
                          {node.name} ({node.ip_address})
                        </option>
                      ))}
                  </select>
                </label>
                <label className="block text-xs font-medium text-slate-700">
                  Destination
                  <select
                    value={destination}
                    onChange={(event) => {
                      setDestination(event.target.value);
                      const target = session.topology.nodes.find(
                        (node) => node.id === event.target.value,
                      );
                      setService(target?.services[0]?.name ?? "");
                    }}
                    className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
                  >
                    <option value="">Select a destination…</option>
                    {session.topology.nodes.map((node) => (
                      <option key={node.id} value={node.id} disabled={node.id === source}>
                        {node.name} ({node.ip_address}, {node.type})
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block text-xs font-medium text-slate-700">
                  Target service
                  <select
                    value={service}
                    onChange={(event) => setService(event.target.value)}
                    className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
                  >
                    <option value="">(no TCP service)</option>
                    {session.topology.nodes
                      .find((node) => node.id === destination)
                      ?.services.map((item) => (
                        <option key={item.name} value={item.name}>
                          {item.name} (port {item.port})
                        </option>
                      )) ?? null}
                  </select>
                </label>
                <p className="rounded border border-slate-200 bg-slate-50 px-2 py-1.5 text-xs text-slate-600">
                  Pick the source, destination and target service in the Diagnostic
                  Workbench when you start a diagnosis. The controls here select a node to
                  inspect.
                </p>
              </div>
            </Card>

            <Card
              title="Fault injection"
              subtitle="Ten fault classes; every option below is validated for this topology."
              className="lg:col-span-2"
            >
              <div className="flex flex-wrap items-end gap-2">
                <label className="min-w-[280px] flex-1 text-xs font-medium text-slate-700">
                  Fault scenario
                  <select
                    value={faultChoice}
                    onChange={(event) => setFaultChoice(event.target.value)}
                    className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
                  >
                    <option value="">Select a fault to inject…</option>
                    {availableByType.map((group) => (
                      <optgroup key={group.type} label={group.type}>
                        {group.items.map((item) => (
                          <option
                            key={faultOptionKey(item)}
                            value={faultOptionKey(item)}
                          >
                            {item.target_label}
                          </option>
                        ))}
                      </optgroup>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  disabled={busy || !faultChoice}
                  onClick={() => void injectFault()}
                  className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
                >
                  Inject fault
                </button>
              </div>

              {(() => {
                const choice = session.available_faults.find(
                  (item) => faultOptionKey(item) === faultChoice,
                );
                if (!choice) return null;
                return (
                  <div className="mt-3 rounded border border-slate-200 bg-slate-50 p-2 text-xs text-slate-700">
                    <p className="font-medium text-slate-900">
                      {choice.fault_type} → {choice.target_label}
                    </p>
                    <p className="mt-0.5">
                      target: <code>{choice.target_id}</code> ({choice.target_kind})
                      {Object.keys(choice.parameters).length > 0 && (
                        <>
                          {" "}
                          · parameters:{" "}
                          <code>{JSON.stringify(choice.parameters)}</code>
                        </>
                      )}
                    </p>
                    <p className="mt-1 text-slate-600">{choice.description}</p>
                  </div>
                );
              })()}

              <div className="mt-4">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-600">
                  Active faults (
                  {session.active_faults.filter((fault) => fault.is_active).length})
                </h3>
                {session.active_faults.length === 0 ? (
                  <p className="mt-1 text-sm text-slate-600">
                    The lab is in its pristine, healthy state.
                  </p>
                ) : (
                  <ul className="mt-1 space-y-2">
                    {session.active_faults.map((fault) => (
                      <li
                        key={fault.id}
                        className={`rounded border p-2 text-xs ${
                          fault.is_active
                            ? "border-red-300 bg-red-50"
                            : "border-slate-300 bg-slate-50"
                        }`}
                      >
                        <div className="flex flex-wrap items-start justify-between gap-2">
                          <div>
                            <p className="font-semibold text-slate-900">
                              {fault.fault_type} → <code>{fault.target_id}</code>{" "}
                              <span className="font-normal">
                                ({fault.target_kind}) · {fault.is_active ? "ACTIVE" : "inactive"}
                              </span>
                            </p>
                            <p className="mt-0.5 text-slate-700">{fault.description}</p>
                            {fault.parameter_summary !== "no parameters" && (
                              <p className="mt-0.5 text-slate-600">
                                {fault.parameter_summary}
                              </p>
                            )}
                          </div>
                          <div className="flex shrink-0 gap-1">
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => void toggleFault(fault)}
                              className="rounded border border-slate-300 bg-white px-2 py-0.5 hover:bg-slate-100 disabled:opacity-50"
                            >
                              {fault.is_active ? "Disable" : "Enable"}
                            </button>
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => void removeFault(fault)}
                              className="rounded border border-red-300 bg-white px-2 py-0.5 text-red-800 hover:bg-red-100 disabled:opacity-50"
                            >
                              Remove
                            </button>
                          </div>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Node details" subtitle="Select a node in the diagram to inspect it.">
              {!selectedNode ? (
                <p className="py-4 text-sm text-slate-600">
                  No node selected. Click any node in the topology.
                </p>
              ) : (
                <dl className="grid grid-cols-2 gap-2 text-xs">
                  <dt className="font-medium text-slate-600">Name</dt>
                  <dd>{selectedNode.name}</dd>
                  <dt className="font-medium text-slate-600">Id</dt>
                  <dd>
                    <code>{selectedNode.id}</code>
                  </dd>
                  <dt className="font-medium text-slate-600">Type</dt>
                  <dd>{selectedNode.type}</dd>
                  <dt className="font-medium text-slate-600">IP address</dt>
                  <dd>
                    <code>{selectedNode.ip_address}</code>
                  </dd>
                  <dt className="font-medium text-slate-600">Default gateway</dt>
                  <dd>{selectedNode.gateway ?? "—"}</dd>
                  <dt className="font-medium text-slate-600">Answers ICMP echo</dt>
                  <dd>{selectedNode.answers_icmp ? "yes" : "no (filtered)"}</dd>
                  {selectedNode.type === "dns_server" && (
                    <>
                      <dt className="font-medium text-slate-600">Resolver enabled</dt>
                      <dd>{selectedNode.resolver_enabled ? "yes" : "no (outage)"}</dd>
                      <dt className="font-medium text-slate-600">Zone records</dt>
                      <dd>
                        {selectedNode.dns_records.map((record) => (
                          <div key={record.name}>
                            <code>
                              {record.name} → {record.address}
                            </code>
                          </div>
                        ))}
                      </dd>
                    </>
                  )}
                  {selectedNode.services.length > 0 && (
                    <>
                      <dt className="font-medium text-slate-600">Services</dt>
                      <dd>
                        {selectedNode.services.map((item) => (
                          <div key={item.name}>
                            {item.name}:{item.port} —{" "}
                            {item.healthy ? "declared healthy" : "declared unhealthy"}
                          </div>
                        ))}
                      </dd>
                    </>
                  )}
                  <dt className="font-medium text-slate-600">Description</dt>
                  <dd className="col-span-2">{selectedNode.description || "—"}</dd>
                </dl>
              )}
            </Card>

            <Card title="Link details" subtitle="All links with their current simulated state.">
              <div className="overflow-x-auto">
                <table className="w-full min-w-[520px] border-collapse text-xs">
                  <caption className="sr-only">Simulated link attributes</caption>
                  <thead>
                    <tr className="border-b border-slate-300 text-left uppercase tracking-wide text-slate-600">
                      <th scope="col" className="py-1 pr-2">Link</th>
                      <th scope="col" className="py-1 pr-2">State</th>
                      <th scope="col" className="py-1 pr-2">Latency</th>
                      <th scope="col" className="py-1 pr-2">Loss</th>
                      <th scope="col" className="py-1">MTU</th>
                    </tr>
                  </thead>
                  <tbody>
                    {session.topology.links.map((link) => (
                      <tr
                        key={link.id}
                        className={
                          highlights.links.has(link.id) ? "bg-red-50 font-medium" : undefined
                        }
                      >
                        <th scope="row" className="py-1 pr-2 text-left font-normal">
                          <code>{link.id}</code>
                          <div className="text-[10px] text-slate-500">
                            {link.node_a} → {link.node_b}
                          </div>
                        </th>
                        <td className="py-1 pr-2">
                          {link.up ? "up" : "DOWN"}
                        </td>
                        <td className="py-1 pr-2 tabular-nums">{link.latency_ms} ms</td>
                        <td className="py-1 pr-2 tabular-nums">
                          {(link.packet_loss_rate * 100).toFixed(0)}%
                        </td>
                        <td className="py-1 tabular-nums">
                          {link.mtu_bytes}
                          {link.suppress_frag_needed ? " (no ICMP err)" : ""}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>

          {Object.keys(session.forwarding_tables).length > 0 && (
            <Card
              title="Derived forwarding tables"
              subtitle="Computed from the current link state by lowest-hop shortest path — a modelled routing layer, not a router OS."
            >
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {Object.entries(session.forwarding_tables).map(([routerId, rows]) => (
                  <div key={routerId} className="rounded border border-slate-200">
                    <p className="border-b border-slate-200 bg-slate-50 px-2 py-1 text-xs font-semibold">
                      {session.topology.nodes.find((node) => node.id === routerId)?.name ??
                        routerId}{" "}
                      <code className="font-normal">({routerId})</code>
                    </p>
                    {rows.length === 0 ? (
                      <p className="px-2 py-1 text-xs text-slate-600">
                        no derived routes for this node
                      </p>
                    ) : (
                      <table className="w-full border-collapse text-[11px]">
                        <thead>
                          <tr className="text-left text-slate-600">
                            <th scope="col" className="px-2 py-1">destination</th>
                            <th scope="col" className="px-2 py-1">next hop</th>
                            <th scope="col" className="px-2 py-1">hops</th>
                          </tr>
                        </thead>
                        <tbody>
                          {rows.map((row) => (
                            <tr key={row.destination} className="border-t border-slate-100">
                              <td className="px-2 py-0.5">
                                <code>{row.destination}</code>
                              </td>
                              <td className="px-2 py-0.5">
                                {row.reachable ? (
                                  <code>{row.next_hop}</code>
                                ) : (
                                  <span className="text-red-800">unreachable</span>
                                )}
                              </td>
                              <td className="px-2 py-0.5 tabular-nums">{row.hops ?? "—"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                ))}
              </div>
            </Card>
          )}

          <Card title="Reset the lab state" subtitle="Reversibility is a first-class property.">
            <EmptyState
              title={`${session.active_faults.length} fault(s) currently defined`}
              description="Every fault is stored as structured data and applied on top of a pristine snapshot, so removing a fault restores the exact original link, MTU, latency, resolver and service state. Re-running a scenario with the same seed reproduces the same observations."
            />
          </Card>
        </>
      )}

      {!session && sessions.data && sessions.data.sessions.length > 0 && (
        <p className="text-xs text-slate-600">
          Tip: load a saved session above, or create a new one from a template.
        </p>
      )}

      {!session && sessionId && (
        <button
          type="button"
          onClick={() => void refreshSession()}
          className="text-xs text-sky-700 underline"
        >
          Reload the active session
        </button>
      )}
    </div>
  );
}
