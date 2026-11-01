import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { AGENT_FRESH_S, useAgentIdle } from "../api/client";
import type { AgentActivityOut, JobOut, ProjectCard, ProjectDetail } from "../api/types";
import { jobStepLabel } from "../components/JobProgress";
import { LATER_GLYPH } from "../components/LaterToggle";
import { modeLabel } from "./modes";
import type { Veil } from "./veil";

/** The render that has the bar's line, wherever it is. */
export interface BarRender {
  job: JobOut;
  /** Named only for another project's render; this project's says the bin alone. */
  projectName: string | null;
  queueDepth: number;
  isCurrent: boolean;
}

/**
 * The tenant ranking's first two rungs: a render in this project outranks one elsewhere. The
 * live list is oldest first; the running one is the line, a queued one stands in until then.
 */
export function pickRender(slug: string | undefined, liveJobs: JobOut[] | undefined, cards: ProjectCard[] | undefined): BarRender | null {
  if (slug && liveJobs && liveJobs.length) {
    const job = liveJobs.find((j) => j.status === "running") ?? liveJobs[0];
    return { job, projectName: null, queueDepth: liveJobs.length - 1, isCurrent: true };
  }
  for (const p of cards ?? []) {
    if (p.slug === slug || !p.generating_job || p.generating <= 0) continue;
    return { job: p.generating_job, projectName: p.name, queueDepth: p.generating - 1, isCurrent: false };
  }
  return null;
}

/** 0..100 for the wash and the edge: the step when there is one, else the runner's percent. */
export function renderProgress(job: JobOut): number {
  if (job.status !== "running") return 0;
  if (job.step != null && job.steps) return Math.max(0, Math.min(100, (job.step / job.steps) * 100));
  if (job.progress != null) return Math.max(0, Math.min(100, job.progress));
  return 0;
}

/** SAMPLING, DECODING, QUEUED… the phase as the bar's word. */
export function phaseWord(job: JobOut): string {
  if (job.status === "queued") return "QUEUED";
  const phase = (job.phase ?? "").trim();
  if (phase && phase !== "queued") return phase.toUpperCase();
  if (phase === "queued") return "QUEUED";
  return (job.task ?? "rendering").toUpperCase();
}

export interface Elsewhere {
  /** sessions waiting or live on other projects */
  live: number;
  /** pending cards + open blockers on other projects' sessions */
  needsYou: number;
}

export function summarizeElsewhere(cards: ProjectCard[] | undefined, current: string | undefined): Elsewhere {
  const out: Elsewhere = { live: 0, needsYou: 0 };
  for (const p of cards ?? []) {
    if (p.slug === current) continue;
    const s = p.agent_session?.status;
    if (s !== "live" && s !== "waiting") continue;
    out.live += 1;
    out.needsYou += p.pending_changes + (p.blocker ? 1 : 0);
  }
  return out;
}

interface Props {
  slug?: string;
  project?: ProjectDetail | null;
  projects?: ProjectCard[];
  /** This project's live renders, oldest first (event-fed). */
  liveJobs?: JobOut[];
  activity?: AgentActivityOut | null;
  /** Server-wide: is an agent CLI running. Undefined while unknown, which reads as available. */
  agentAvailable?: boolean;
  panelOpen: boolean;
  /** Below 760: the bar sheds words, the toggle reads ▣ / ▣ close. */
  phone: boolean;
  starting?: boolean;
  /** This project's saved group: generations held for later. The pill releases them all. */
  heldCount?: number;
  releasing?: boolean;
  onRelease?: () => void;
  onTogglePanel: () => void;
  onStart: () => void;
  /** After a session ends: `31 changes` opens the panel read-only. */
  onOpenTranscript: () => void;
  /** A private project's veil over its pictures: on a phone the bar carries the pill that lifts it. */
  veil?: Veil;
}

/**
 * The system bar: the machine's, on every screen. It reports ComfyUI renders and queue depth
 * whether or not an agent is installed; the agent is a tenant in it. One tenant has the line
 * at a time -- render here, render elsewhere, agent phrase, counts, else `idle · 0 queued` --
 * and the loser collapses to a chip. Blockers never take the line: the whole signal is an
 * amber count on the panel toggle. The right end is the agent's only front door in the app.
 */
export function SystemBar({
  slug,
  project,
  projects,
  liveJobs,
  activity,
  agentAvailable,
  panelOpen,
  phone,
  starting,
  heldCount = 0,
  releasing,
  onRelease,
  onTogglePanel,
  onStart,
  onOpenTranscript,
  veil,
}: Props) {
  const session = project?.agent_session ?? null;
  const status = session?.status ?? "none";
  const sessionOn = status === "waiting" || status === "live";
  const ended = status === "ended";
  const pending = (project?.pending_changes ?? 0) + (project?.blocker ? 1 : 0);
  const idle = useAgentIdle(activity);
  const doing = (activity?.doing ?? "").trim();
  // "waiting for you" is the agent's watch verb: standing by, not working
  const working = status === "live" && idle != null && idle < AGENT_FRESH_S && !!doing && !/^waiting/i.test(doing);
  const phrase = status === "waiting" ? "connecting…" : working ? doing : pending > 0 ? `holding · ${pending} pending` : "standing by";

  const render = pickRender(slug, liveJobs, projects);
  const progress = render ? renderProgress(render.job) : 0;
  const elsewhere = summarizeElsewhere(projects, slug);
  const queued = slug ? (liveJobs?.length ?? 0) : (projects ?? []).reduce((n, p) => n + p.generating, 0);
  const absent = agentAvailable === false && !sessionOn;
  // a private project has no agent: the server hides it from the agent and refuses its calls,
  // so the bar offers no Start, the same way it offers none when no agent CLI is running
  const locked = !!project?.private && !sessionOn;

  const [explain, setExplain] = useState(false);
  const explainer = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!explain) return;
    const away = (e: MouseEvent | TouchEvent) => {
      if (!explainer.current?.contains(e.target as Node)) setExplain(false);
    };
    const key = (e: KeyboardEvent) => e.key === "Escape" && setExplain(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("touchstart", away);
    window.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("touchstart", away);
      window.removeEventListener("keydown", key);
    };
  }, [explain]);

  const dotClass = working ? "is-working" : "is-standby";

  const line = render ? (
    <span className="bar-line" aria-live="polite">
      <span className={`dot ${render.isCurrent ? "is-render" : "is-render-remote"}`} />
      <span className="bar-word">{phaseWord(render.job)}</span>
      {jobStepLabel(render.job) && <span className="bar-step">{jobStepLabel(render.job)}</span>}
      <span className="bar-sep">·</span>
      {render.isCurrent ? (
        <span className="bar-bin">{render.job.bin_name || binName(project, render.job.bin)}</span>
      ) : (
        <>
          <span className="bar-project">{render.projectName}</span>
          {/* a private project's card carries no bin name: the project alone, no "/ bin" stand-in */}
          {render.job.bin_name && (
            <>
              <span className="bar-sep">/</span>
              <span className="bar-remote-bin">{render.job.bin_name}</span>
            </>
          )}
        </>
      )}
      {render.queueDepth > 0 && <span className="bar-queued">{phone ? `+${render.queueDepth}` : `+${render.queueDepth} queued`}</span>}
      {render.isCurrent && render.job.queued_by && <span className="bar-attrib">queued by {render.job.queued_by === "agent" ? "agent" : "you"}</span>}
    </span>
  ) : sessionOn && session ? (
    <span className="bar-line" aria-live="polite">
      <span className={`dot ${dotClass}`} />
      <span className="bar-word">AGENT</span>
      <span className="bar-phrase">{phrase}</span>
    </span>
  ) : ended ? (
    <span className="bar-line">
      <span className="dot is-ended" />
      <span className="bar-ended">session ended</span>
      <span className="bar-sep">·</span>
      <button type="button" className="bar-link" onClick={onOpenTranscript} title="open the session's transcript">
        {project?.session_changes ?? 0} {project?.session_changes === 1 ? "change" : "changes"}
      </button>
    </span>
  ) : absent || locked ? null : (
    <span className="bar-idle">idle · {queued} queued</span>
  );

  const showElsewhere = elsewhere.live > 0 || elsewhere.needsYou > 0;
  const liveWord = slug ? "live elsewhere" : elsewhere.live === 1 ? "session live" : "sessions live";

  let control: JSX.Element | null = null;
  if (locked) {
    control = (
      <button type="button" className="bar-pill is-absent" onClick={() => setExplain(!explain)} aria-expanded={explain} title="a private project has no agent">
        <span className="dot" />
        private
      </button>
    );
  } else if (absent) {
    control = (
      <button type="button" className="bar-pill is-absent" onClick={() => setExplain(!explain)} aria-expanded={explain} title="no agent CLI is running against this server">
        <span className="dot" />
        no agent
      </button>
    );
  } else if (sessionOn) {
    const label = phone ? (panelOpen ? "▣ close" : "▣") : "▣ panel";
    control = (
      <button
        type="button"
        className={`bar-pill bar-toggle ${panelOpen ? "is-open" : ""}`}
        onClick={onTogglePanel}
        aria-pressed={panelOpen}
        title={panelOpen ? "close the panel — the session keeps running" : pending ? `${pending} waiting for you: open the panel` : "open the agent panel"}
      >
        {label}
        {pending > 0 && <span className="badge-amber">{pending}</span>}
      </button>
    );
  } else if (slug) {
    control = (
      <button type="button" className="bar-pill" onClick={onStart} disabled={starting} title="start a session in the last-used mode; notes go in the panel">
        ✦ agent
        <span className="bar-pill-go">{starting ? "starting…" : "start →"}</span>
      </button>
    );
  }

  return (
    <div className={`bar ${render && !render.isCurrent ? "is-remote" : ""}`} role="status">
      {render && (
        <>
          <span className="bar-wash" style={{ width: `${progress}%` }} aria-hidden="true" />
          <span className="bar-edge" style={{ width: `${progress}%` }} aria-hidden="true" />
        </>
      )}
      <Link to="/" className="bar-home" aria-label="Home" title="projects">
        <img src="/apple-touch-icon.png" alt="" />
      </Link>
      {line}
      <span className="spacer" />
      {showElsewhere && (
        <span className="bar-elsewhere">
          {elsewhere.live > 0 && (
            <span className="bar-count">
              {!slug && <span className="dot is-working" aria-hidden="true" />}
              {elsewhere.live} {liveWord}
            </span>
          )}
          {elsewhere.live > 0 && elsewhere.needsYou > 0 && <span className="bar-sep">·</span>}
          {elsewhere.needsYou > 0 && (
            <span className="bar-count">
              <span className="badge-amber is-small">{elsewhere.needsYou}</span>
              {elsewhere.needsYou === 1 ? "needs you" : "need you"}
            </span>
          )}
        </span>
      )}
      {render && sessionOn && session && (
        <span className="bar-chip" title={phrase}>
          <span className={`dot ${dotClass}`} />
          {modeLabel(session.mode)}
        </span>
      )}
      {slug && heldCount > 0 && (
        <button
          type="button"
          className="bar-pill bar-held"
          onClick={onRelease}
          disabled={releasing || !onRelease}
          title={`send ${heldCount === 1 ? "the generation" : `all ${heldCount} generations`} saved for later to ComfyUI now, in the order saved`}
        >
          {LATER_GLYPH} {heldCount}
          {!phone && " saved"}
          <span className="bar-pill-go">{releasing ? "starting…" : "start →"}</span>
        </button>
      )}
      {phone && !!project?.private && veil && (
        <button
          type="button"
          className={`bar-veil ${veil.revealed ? "is-open" : ""}`}
          onClick={veil.toggle}
          aria-pressed={veil.revealed}
          aria-label={veil.revealed ? "hide this project's pictures" : "show this project's pictures"}
          title={veil.revealed ? "unlocked — tap to hide this project's pictures again" : "locked — tap to show this project's pictures until you leave this page"}
        >
          <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
            <rect x="3" y="7" width="10" height="7" rx="1.5" fill="currentColor" />
            {veil.revealed ? (
              /* open shackle: swung up and to the side */
              <path d="M5 7V4.5a3 3 0 0 1 6 0" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" transform="translate(4.5 -1) rotate(20 8 5)" />
            ) : (
              <path d="M5 7V4.5a3 3 0 0 1 6 0V7" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            )}
          </svg>
        </button>
      )}
      {control}
      {explain && locked && (
        <div ref={explainer} className="bar-explainer" role="dialog" aria-label="Private project">
          <b>Private project</b>
          <span>
            The agent cannot see this project: it is left out of the agent's list and every agent call to it is refused. Turn private off in project settings and this becomes <code>✦ agent</code>.
          </span>
        </div>
      )}
      {explain && !locked && (
        <div ref={explainer} className="bar-explainer" role="dialog" aria-label="No agent on this server">
          <b>No agent on this server</b>
          <span>
            ComfyUI is connected and renders normally. The agent CLI is not running — start it on the machine and this becomes <code>✦ agent</code>.
          </span>
        </div>
      )}
    </div>
  );
}

function binName(project: ProjectDetail | null | undefined, binId: string): string {
  return project?.bins.find((b) => b.id === binId)?.name ?? "bin";
}
