import type { JobOut } from "../api/types";

/** The live fields a job carries; the enhancer's progress event carries the same ones. */
export type LiveLike = Pick<JobOut, "status" | "task" | "phase" | "step" | "steps" | "pass_no" | "passes" | "queue_ahead" | "queue_ahead_ours">;

/**
 * What a live job is doing, for the footer beside Generate. Two pieces:
 *
 * - `jobPhaseLine` — the phase, for the bar: `queued · 2 ahead (1 yours · 1 external)`,
 *   `loading models`, `sampling · pass 2/2`, `decoding`, `saving`, `finishing`. The phase
 *   names come from the runner classifying whatever node ComfyUI is executing, so any
 *   workflow's stages show up, named ones or not (an unknown node shows its class name).
 * - `jobStepLabel` — the step counter, for the button: `14/30` whenever the current node
 *   reports one (samplers always; some decoders and combiners too), else null.
 *
 * Before the runner reports a phase, the runner's own task line (`Preparing graph`) stands in.
 */

export function jobPhaseLine(job: LiveLike | null | undefined): string | null {
  if (!job || (job.status !== "queued" && job.status !== "running")) return null;
  const phase = job.phase ?? null;
  if (!phase) return job.task ? job.task.toLowerCase() : job.status === "queued" ? "queued" : null;
  if (phase === "queued") {
    const ahead = job.queue_ahead;
    if (ahead == null) return "queued";
    if (ahead === 0) return "queued · next up";
    const ours = job.queue_ahead_ours ?? 0;
    const ext = ahead - ours;
    let detail = `${ahead} ahead`;
    if (ours && ext) detail += ` (${ours} yours · ${ext} external)`;
    else if (ours) detail += " (yours)";
    else if (ext) detail += " (external)";
    return `queued · ${detail}`;
  }
  if (phase === "sampling" && (job.passes ?? 0) > 1) return `sampling · pass ${job.pass_no ?? 1}/${job.passes}`;
  return phase;
}

/** ComfyUI's execution time for a finished job as `42s` / `3m 12s` / `1h 04m`, or null when
 * the runner did not report one (a failure, or an older runner). The render alone: queue
 * wait and THM's landing are not in it. */
export function execTimeLabel(seconds: number | null | undefined): string | null {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return null;
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}

export function jobStepLabel(job: LiveLike | null | undefined): string | null {
  if (!job || job.status !== "running") return null;
  if (job.step == null || !job.steps) return null;
  return `${job.step}/${job.steps}`;
}

/** The phase line as a footer element; renders nothing when there is nothing to say. */
export function JobStatusLine({ job }: { job: LiveLike | null | undefined }) {
  const line = jobPhaseLine(job);
  if (!line) return null;
  return (
    <span className="job-status" aria-live="polite" title={job?.task ?? undefined}>
      {line}
    </span>
  );
}
