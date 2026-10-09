/**
 * Application shell: routing, backend health banner and the active-session state
 * that the Lab, Workbench and Report pages share.
 *
 * The session context is deliberately the *only* shared mutable state in the app;
 * everything else is fetched per page, which keeps a page's numbers traceable to a
 * single request.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { NavLink, Route, Routes, useNavigate } from "react-router-dom";
import {
  Activity,
  AlertTriangle,
  FlaskConical,
  LayoutDashboard,
  Network,
  Stethoscope,
} from "lucide-react";

import { api, ApiError, type Diagnosis, type HealthResponse, type LabSession } from "./lib/api";
import { useAsync } from "./lib/useAsync";
import { ModeBadge } from "./components/ui";
import OverviewPage from "./pages/OverviewPage";
import LabPage from "./pages/LabPage";
import WorkbenchPage from "./pages/WorkbenchPage";
import ReportPage from "./pages/ReportPage";
import ExperimentsPage from "./pages/ExperimentsPage";

interface SessionContextValue {
  session: LabSession | null;
  sessionId: string | null;
  setSession: (session: LabSession | null) => void;
  setSessionId: (sessionId: string | null) => void;
  refreshSession: () => Promise<void>;
  diagnosisId: string | null;
  setDiagnosisId: (diagnosisId: string | null) => void;
  diagnosis: Diagnosis | null;
  setDiagnosis: (diagnosis: Diagnosis | null) => void;
  modelVersion: string;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function useSessionContext(): SessionContextValue {
  const context = useContext(SessionContext);
  if (!context) throw new Error("useSessionContext must be used inside the app shell");
  return context;
}

const NAV = [
  { to: "/", label: "Overview", icon: LayoutDashboard },
  { to: "/lab", label: "Network Lab", icon: Network },
  { to: "/diagnose", label: "Diagnostic Workbench", icon: Stethoscope },
  { to: "/report", label: "Evidence & Report", icon: Activity },
  { to: "/experiments", label: "Experiment Studio", icon: FlaskConical },
];

export default function App() {
  const navigate = useNavigate();
  const [session, setSession] = useState<LabSession | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [diagnosis, setDiagnosis] = useState<Diagnosis | null>(null);
  const [diagnosisId, setDiagnosisId] = useState<string | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthError, setHealthError] = useState<ApiError | null>(null);

  const healthQuery = useAsync(() => api.health(), []);

  useEffect(() => {
    if (healthQuery.data) {
      setHealth(healthQuery.data);
      setHealthError(null);
    }
    if (healthQuery.error) setHealthError(healthQuery.error);
  }, [healthQuery.data, healthQuery.error]);

  const refreshSession = useCallback(async () => {
    if (!sessionId) return;
    const updated = await api.getSession(sessionId);
    setSession(updated);
  }, [sessionId]);

  const value = useMemo<SessionContextValue>(
    () => ({
      session,
      sessionId,
      setSession: (next) => {
        setSession(next);
        setSessionId(next?.id ?? null);
        setDiagnosis(null);
        setDiagnosisId(null);
      },
      setSessionId,
      refreshSession,
      diagnosisId,
      setDiagnosisId,
      diagnosis,
      setDiagnosis,
      modelVersion: health?.model_version ?? "unknown",
    }),
    [session, sessionId, refreshSession, diagnosisId, diagnosis, health],
  );

  return (
    <SessionContext.Provider value={value}>
      <div className="min-h-screen bg-slate-100 text-slate-900">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2 focus:text-sm focus:shadow"
        >
          Skip to main content
        </a>

        <header className="border-b border-slate-300 bg-white">
          <div className="mx-auto flex max-w-[1400px] flex-wrap items-center justify-between gap-3 px-4 py-3">
            <div className="flex items-center gap-3">
              <span className="flex h-9 w-9 items-center justify-center rounded bg-slate-900 text-sm font-bold text-white">
                NS
              </span>
              <div>
                <h1 className="text-base font-semibold leading-tight">
                  NetSleuth — Evidence-Guided Fault Localization
                </h1>
                <p className="text-xs text-slate-600">
                  Adaptive diagnostic probing in a deterministic virtual network lab
                </p>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <ModeBadge mode="SIMULATED LAB" />
              {health ? (
                <span className="rounded border border-slate-300 bg-white px-2 py-0.5 text-xs text-slate-700">
                  backend v{health.version} · model{" "}
                  <code className="text-[11px]">{health.model_version}</code>
                </span>
              ) : healthError ? (
                <span className="inline-flex items-center gap-1 rounded border border-red-300 bg-red-50 px-2 py-0.5 text-xs font-medium text-red-900">
                  <AlertTriangle size={12} aria-hidden="true" />
                  backend unreachable
                </span>
              ) : (
                <span className="text-xs text-slate-500">checking backend…</span>
              )}
            </div>
          </div>
          <nav aria-label="Main" className="mx-auto max-w-[1400px] px-4">
            <ul className="flex flex-wrap gap-1">
              {NAV.map((item) => {
                const Icon = item.icon;
                return (
                  <li key={item.to}>
                    <NavLink
                      to={item.to}
                      end={item.to === "/"}
                      className={({ isActive }) =>
                        `inline-flex items-center gap-1.5 rounded-t border-b-2 px-3 py-2 text-sm font-medium ${
                          isActive
                            ? "border-slate-900 text-slate-900"
                            : "border-transparent text-slate-600 hover:border-slate-300 hover:text-slate-900"
                        }`
                      }
                    >
                      <Icon size={15} aria-hidden="true" />
                      {item.label}
                    </NavLink>
                  </li>
                );
              })}
            </ul>
          </nav>
        </header>

        {healthError && (
          <div role="alert" className="border-b border-red-300 bg-red-50 px-4 py-2 text-sm text-red-900">
            <div className="mx-auto max-w-[1400px]">
              <strong>The backend is not responding.</strong> {healthError.message} Start it
              with <code className="rounded bg-white px-1">uvicorn app.main:app --port 8000</code>{" "}
              from the <code className="rounded bg-white px-1">backend</code> directory.
            </div>
          </div>
        )}

        {session && (
          <div className="border-b border-slate-200 bg-slate-50 px-4 py-1.5 text-xs text-slate-700">
            <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-4 gap-y-1">
              <span>
                Active session <code>{session.id}</code> · {session.template_name}
              </span>
              <span>
                active faults: <strong>{session.active_faults.filter((f) => f.is_active).length}</strong>
              </span>
              {diagnosis && (
                <span>
                  diagnosis <code>{diagnosis.id}</code> · {diagnosis.status} ·{" "}
                  {diagnosis.probes_used}/{diagnosis.max_probes} probes
                </span>
              )}
              <button
                type="button"
                onClick={() => navigate("/lab")}
                className="rounded border border-slate-300 bg-white px-1.5 py-0.5 hover:bg-slate-100"
              >
                open lab
              </button>
            </div>
          </div>
        )}

        <main id="main" className="mx-auto max-w-[1400px] px-4 py-5">
          <Routes>
            <Route path="/" element={<OverviewPage />} />
            <Route path="/lab" element={<LabPage />} />
            <Route path="/diagnose" element={<WorkbenchPage />} />
            <Route path="/report" element={<ReportPage />} />
            <Route path="/experiments" element={<ExperimentsPage />} />
          </Routes>
        </main>

        <footer className="mt-8 border-t border-slate-300 bg-white px-4 py-4 text-xs text-slate-600">
          <div className="mx-auto max-w-[1400px] space-y-1">
            <p>
              All diagnostics in this build are produced by the deterministic virtual lab and are
              labelled <strong>SIMULATED LAB</strong>. No live network probes are performed and no
              scanning, exploitation or traffic generation is implemented.
            </p>
            <p>
              Diagnosis confidence is a relative ranking of modelled explanations computed from
              recorded observations — it is not a physical measurement of your network.
            </p>
          </div>
        </footer>
      </div>
    </SessionContext.Provider>
  );
}
