/**
 * Values the UI and its tests must agree on.
 *
 * `TERMINAL_STATUSES` deliberately lives in one place. The backend decides terminality
 * (see `app.diagnosis.planner.Status`), and the UI only needs to know when to stop
 * offering the step/run controls; duplicating the list across pages meant a new backend
 * status could silently leave buttons enabled on a finished run. There is a test that
 * asserts this list matches the backend's terminal set.
 */

export const TERMINAL_STATUSES = [
  "confident",
  "inconclusive",
  "budget_exhausted",
  "error",
] as const;

export type TerminalStatus = (typeof TERMINAL_STATUSES)[number];

const TERMINAL = new Set<string>(TERMINAL_STATUSES);

/** True when the backend has finished with this diagnosis. */
export function isTerminalStatus(status: string): boolean {
  return TERMINAL.has(status);
}
