/** Shared presentational primitives. Icons and text carry meaning, never colour alone. */

import type { ReactNode } from "react";

import type { ApiError } from "../lib/api";

export function ModeBadge({ mode }: { mode: string }) {
  const isLive = mode.includes("LIVE");
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs font-semibold uppercase tracking-wide ${
        isLive
          ? "border-red-300 bg-red-50 text-red-800"
          : "border-sky-300 bg-sky-50 text-sky-900"
      }`}
    >
      <span aria-hidden="true" className="text-[10px] leading-none">
        {isLive ? "●" : "◇"}
      </span>
      {mode}
    </span>
  );
}

const STATUS_STYLES: Record<string, string> = {
  confident: "border-green-300 bg-green-50 text-green-900",
  inconclusive: "border-amber-300 bg-amber-50 text-amber-900",
  budget_exhausted: "border-orange-300 bg-orange-50 text-orange-900",
  error: "border-red-300 bg-red-50 text-red-900",
  running: "border-slate-300 bg-slate-50 text-slate-800",
};

const STATUS_SYMBOLS: Record<string, string> = {
  confident: "✓",
  inconclusive: "?",
  budget_exhausted: "!",
  error: "×",
  running: "…",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs font-semibold ${
        STATUS_STYLES[status] ?? STATUS_STYLES.running
      }`}
      title={status}
    >
      <span aria-hidden="true">{STATUS_SYMBOLS[status] ?? "•"}</span>
      {status.replace(/_/g, " ")}
    </span>
  );
}

export function Card({
  title,
  subtitle,
  actions,
  children,
  className = "",
}: {
  title?: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-lg border border-slate-200 bg-white shadow-sm ${className}`}>
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 px-4 py-3">
          <div>
            {title && <h2 className="text-sm font-semibold text-slate-900">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-slate-600">{subtitle}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className="px-4 py-3">{children}</div>
    </section>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div role="status" aria-live="polite" className="flex items-center gap-2 py-6 text-sm text-slate-600">
      <span
        aria-hidden="true"
        className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-slate-300 border-t-slate-600"
      />
      {label}
    </div>
  );
}

export function ErrorNotice({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  return (
    <div role="alert" className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900">
      <p className="font-semibold">{error.readable}</p>
      <p className="mt-0.5 text-xs text-red-800">
        Error code: <code>{error.code}</code>
        {error.status ? ` · HTTP ${error.status}` : ""}
      </p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-2 rounded border border-red-400 bg-white px-2 py-1 text-xs font-medium text-red-900 hover:bg-red-100"
        >
          Retry
        </button>
      )}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="rounded border border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-center">
      <p className="text-sm font-semibold text-slate-800">{title}</p>
      <p className="mx-auto mt-1 max-w-xl text-xs text-slate-600">{description}</p>
      {action && <div className="mt-3 flex justify-center">{action}</div>}
    </div>
  );
}

/** A horizontal probability bar. The numeric value is always shown as text too. */
export function ProbabilityBar({
  value,
  label,
  emphasis = false,
}: {
  value: number;
  label?: string;
  emphasis?: boolean;
}) {
  const width = Math.max(0, Math.min(100, value * 100));
  return (
    <div className="flex items-center gap-2">
      <div
        className="h-2.5 flex-1 overflow-hidden rounded-full bg-slate-200"
        role="img"
        aria-label={`${(value * 100).toFixed(1)} percent${label ? ` for ${label}` : ""}`}
      >
        <div
          className={`h-full rounded-full ${emphasis ? "bg-sky-600" : "bg-slate-400"}`}
          style={{ width: `${width}%` }}
        />
      </div>
      <span className="w-14 shrink-0 text-right font-mono text-xs tabular-nums text-slate-700">
        {(value * 100).toFixed(1)}%
      </span>
    </div>
  );
}

export function Metric({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
}) {
  return (
    <div className="rounded border border-slate-200 bg-slate-50 px-3 py-2">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-0.5 font-mono text-lg tabular-nums text-slate-900">{value}</p>
      {hint && <p className="mt-0.5 text-xs text-slate-600">{hint}</p>}
    </div>
  );
}

export function StrengthTag({ strength }: { strength: string }) {
  const symbol = strength === "strong" ? "■■■" : strength === "moderate" ? "■■□" : "■□□";
  return (
    <span className="inline-flex items-center gap-1 rounded border border-slate-300 bg-white px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-slate-700">
      <span aria-hidden="true" className="font-mono">{symbol}</span>
      {strength}
    </span>
  );
}

export function KindTag({ kind }: { kind: string }) {
  const styles: Record<string, string> = {
    observation: "border-slate-400 bg-white text-slate-800",
    reading: "border-sky-400 bg-sky-50 text-sky-900",
    limitation: "border-amber-400 bg-amber-50 text-amber-900",
  };
  return (
    <span
      className={`inline-flex rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${
        styles[kind] ?? styles.observation
      }`}
    >
      {kind}
    </span>
  );
}
