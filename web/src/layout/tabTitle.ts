import { jobPhaseLine, jobStepLabel, type LiveLike } from "../components/JobProgress";

/**
 * The tab title follows a generation while you are on another tab: `THM 2.0 loading models`,
 * `THM 2.0 6/12`, then `THM 2.0 ✓ done` (or `✕ failed`) held until you come back.
 *
 * It is written straight from the event stream and the fetches, not from a React effect, because
 * a hidden tab throttles the timers React Query and React schedule through (Chrome: once a
 * second at first, once a minute after five minutes) while an EventSource message or a fetch
 * callback still runs at once. That covers the wait between Generate and a short render; a tab
 * left hidden for a very long time can be frozen outright by the browser, which nothing here can
 * work around. `scope` is the project the app is showing (null on the home screen), so a late
 * event for another project cannot overwrite the title.
 */

const BASE = "THM 2.0";
let scope: string | null | undefined;
let lastJobs: (LiveLike | null | undefined)[] = [];
/** The finish notice shown while the tab is hidden; a visibilitychange clears it. */
let held: string | null = null;

export function setTitleScope(slug: string | null) {
  scope = slug;
  held = null;
}

export function titleFor(jobs: (LiveLike | null | undefined)[]): string {
  const live = jobs.filter((j): j is LiveLike => !!j && (j.status === "queued" || j.status === "running"));
  if (!live.length) return BASE;
  // the step counter when a node reports one, else the phase (`sampling`, `loading models`)
  const label = live.map((j) => jobStepLabel(j)).find(Boolean) ?? live.map((j) => jobPhaseLine(j)).find(Boolean) ?? "generating";
  return `${BASE} ${label}`;
}

function write(next: string) {
  if (document.title !== next) document.title = next;
}

/** Set the title from `jobs` when they belong to what the app is showing. `finished` is a job
 * that just ended: while the tab is hidden its verdict is held in the title until you return. */
export function applyTitle(slug: string | null, jobs: (LiveLike | null | undefined)[], finished?: LiveLike | null) {
  if (scope === undefined || slug !== scope) return;
  lastJobs = jobs;
  const next = titleFor(jobs);
  if (finished && document.hidden && next === BASE) {
    held = finished.status === "done" ? `${BASE} ✓ done` : finished.status === "failed" ? `${BASE} ✕ failed` : null;
  }
  write(held ?? next);
}

if (typeof document !== "undefined") {
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    held = null;
    write(titleFor(lastJobs));
  });
}
