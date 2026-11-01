import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useHealth } from "../api/client";
import type { JobOut, ProjectCard, ProjectDetail } from "../api/types";
import { jobStepLabel } from "../components/JobProgress";
import { LATER_GLYPH } from "../components/LaterToggle";
import { VeilGlyph } from "../components/VeilGlyph";
import type { Veil } from "./veil";

/** The bar's leading status dot, as a link to the ComfyUI renders go to (a new tab), when its
 * address is known: one click from any screen to the machine's queue. */
function StatusDot({ className, url }: { className: string; url: string }) {
  if (!url) return <span className={`dot ${className}`} />;
  return (
    <a className="dot-link" href={url} target="_blank" rel="noopener noreferrer" title={`open ComfyUI (${url}) in a new tab`} aria-label="Open ComfyUI in a new tab">
      <span className={`dot ${className}`} />
    </a>
  );
}

/** The render that has the bar's line: the machine's, whichever project it is for. */
export interface BarRender {
  job: JobOut;
  /** The project it is for; a private one gives its name and nothing more (no bin). */
  projectName: string;
  /** Where the names link: the project, and its bin (none for a private one). */
  slug: string;
  binId: string | null;
  binName: string | null;
  /** Everything else waiting on the machine, every project. */
  queueDepth: number;
}

/**
 * The bar's line: what ComfyUI is running now, in any project, else the first thing queued.
 * No project is favoured: the open one's live jobs (event-fed, fresher than its card) stand in
 * for its card and are ranked with everyone else's. A private project shows its name only.
 */
export function pickRender(slug: string | undefined, liveJobs: JobOut[] | undefined, cards: ProjectCard[] | undefined): BarRender | null {
  const pool: { job: JobOut; card: ProjectCard }[] = [];
  let total = 0;
  for (const card of cards ?? []) {
    if (card.slug === slug && liveJobs) {
      total += liveJobs.length;
      for (const job of liveJobs) pool.push({ job, card });
    } else if (card.generating_job && card.generating > 0) {
      total += card.generating;
      pool.push({ job: card.generating_job, card });
    }
  }
  // "running" covers a job ComfyUI has accepted but not started (phase still "queued"): the one
  // actually executing wins, then one handed over, then the first still waiting here
  const executing = (j: JobOut) => j.status === "running" && (j.phase ?? "") !== "queued";
  const pick = pool.find((x) => executing(x.job)) ?? pool.find((x) => x.job.status === "running") ?? pool[0];
  if (!pick) return null;
  const priv = !!pick.card.private;
  return {
    job: pick.job,
    projectName: pick.card.name,
    slug: pick.card.slug,
    binId: priv ? null : pick.job.bin || null,
    binName: priv ? null : pick.job.bin_name || null,
    queueDepth: Math.max(0, total - 1),
  };
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
  if (job.stalled) return "STALLED"; // no progress for a while: the feed has the alert line
  const phase = (job.phase ?? "").trim();
  if (phase && phase !== "queued") return phase.toUpperCase();
  if (phase === "queued") return "QUEUED";
  return (job.task ?? "generating").toUpperCase();
}

export interface Elsewhere {
  /** sessions waiting or live, every project */
  live: number;
  /** pending cards + open blockers on every project's session */
  needsYou: number;
}

/** What one project is asking of you: a session that is up, and how many things it owes an
 * answer on. The bar sums these over every project; the project list shows them per row, so
 * `1 needs you · 3 sessions live` can be traced to the project it came from. */
export function projectAttention(p: ProjectCard): Elsewhere {
  const s = p.agent_session?.status;
  if (s !== "live" && s !== "waiting") return { live: 0, needsYou: 0 };
  return { live: 1, needsYou: p.pending_changes + (p.blocker ? 1 : 0) };
}

export function summarizeAll(cards: ProjectCard[] | undefined): Elsewhere {
  const out: Elsewhere = { live: 0, needsYou: 0 };
  for (const p of cards ?? []) {
    const one = projectAttention(p);
    out.live += one.live;
    out.needsYou += one.needsYou;
  }
  return out;
}

interface Props {
  slug?: string;
  project?: ProjectDetail | null;
  projects?: ProjectCard[];
  /** This project's live renders, oldest first (event-fed). */
  liveJobs?: JobOut[];
  /** Server-wide: is an agent CLI running. Undefined while unknown, which reads as available. */
  agentAvailable?: boolean;
  panelOpen: boolean;
  /** Below 760: the bar sheds words, the toggle reads ▣ / ▣ close. */
  phone: boolean;
  starting?: boolean;
  /** The machine's saved group: generations held for later in every project. The pill releases them all, oldest first. */
  heldCount?: number;
  releasing?: boolean;
  onRelease?: () => void;
  onTogglePanel: () => void;
  onStart: () => void;
  /** A private project's veil over its pictures: on a phone the bar carries the pill that lifts it. */
  veil?: Veil;
}

/**
 * The system bar: the machine's, on every screen, and about no one project. It reports what
 * ComfyUI is rendering (whichever project it is for) and how deep the queue is, how many
 * sessions are up and how many things wait on you across every project. The open project's own
 * state lives in the screen and its panel; the right end is only the panel's door.
 */
export function SystemBar({
  slug,
  project,
  projects,
  liveJobs,
  agentAvailable,
  panelOpen,
  phone,
  starting,
  heldCount = 0,
  releasing,
  onRelease,
  onTogglePanel,
  onStart,
  veil,
}: Props) {
  const session = project?.agent_session ?? null;
  const status = session?.status ?? "none";
  const sessionOn = status === "waiting" || status === "live";
  const pending = (project?.pending_changes ?? 0) + (project?.blocker ? 1 : 0);

  const render = pickRender(slug, liveJobs, projects);
  const progress = render ? renderProgress(render.job) : 0;
  const all = summarizeAll(projects);
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

  // this project's ComfyUI when it names one, else the server's
  const health = useHealth();
  const comfyUrl = (project?.comfy?.api_base || health.data?.comfy_url || "").trim();

  const line = render ? (
    <span className="bar-line" aria-live="polite">
      <StatusDot className="is-render" url={comfyUrl} />
      <span className="bar-word">{phaseWord(render.job)}</span>
      {jobStepLabel(render.job) && <span className="bar-step">{jobStepLabel(render.job)}</span>}
      <span className="bar-sep">·</span>
      <Link to={`/p/${encodeURIComponent(render.slug)}`} className="bar-project" title={`open ${render.projectName}`}>
        {render.projectName}
      </Link>
      {render.binName && (
        <>
          <span className="bar-sep">/</span>
          {render.binId ? (
            <Link to={`/p/${encodeURIComponent(render.slug)}/b/${encodeURIComponent(render.binId)}`} className="bar-remote-bin" title={`open ${render.binName}`}>
              {render.binName}
            </Link>
          ) : (
            <span className="bar-remote-bin">{render.binName}</span>
          )}
        </>
      )}
      {render.queueDepth > 0 && <span className="bar-queued">{phone ? `+${render.queueDepth}` : `+${render.queueDepth} queued`}</span>}
    </span>
  ) : (
    <span className="bar-line">
      <StatusDot className="is-standby" url={comfyUrl} />
      <span className="bar-idle">idle</span>
    </span>
  );

  const showAll = all.live > 0 || all.needsYou > 0;
  // leaving with the panel open: Home sends a project that owes you something straight back into it
  const homeState = panelOpen ? { fromAgent: true } : undefined;

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
    control = (
      <button
        type="button"
        className={`bar-pill bar-toggle ${panelOpen ? "is-open" : ""}`}
        onClick={onTogglePanel}
        aria-pressed={panelOpen}
        aria-label={panelOpen ? "close the agent panel" : "open the agent panel"}
        title={panelOpen ? "close the panel — the session keeps running" : pending ? `${pending} waiting for you: open the panel` : "open the agent panel"}
      >
        <PanelGlyph open={panelOpen} />
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
    <div className="bar" role="status">
      {render && (
        <>
          <span className="bar-wash" style={{ width: `${progress}%` }} aria-hidden="true" />
          <span className="bar-edge" style={{ width: `${progress}%` }} aria-hidden="true" />
        </>
      )}
      <Link to="/" state={homeState} className="bar-home" aria-label="Home" title="projects">
        <img src="/apple-touch-icon.png" alt="" />
      </Link>
      {line}
      <span className="spacer" />
      {showAll && (
        <span className="bar-elsewhere">
          {all.needsYou > 0 && (
            <Link to="/" state={homeState} className="bar-count is-link" title="projects: see which ones are waiting on you">
              <span className="badge-amber is-small">{all.needsYou}</span>
              {all.needsYou === 1 ? "needs you" : "need you"}
            </Link>
          )}
          {all.live > 0 && all.needsYou > 0 && <span className="bar-sep">·</span>}
          {all.live > 0 && (
            <Link to="/" state={homeState} className="bar-count is-link" title="projects: see which sessions are live">
              <span className="dot is-working" aria-hidden="true" />
              {all.live} {all.live === 1 ? "session live" : "sessions live"}
            </Link>
          )}
        </span>
      )}
      {heldCount > 0 && (
        <button
          type="button"
          className="bar-pill bar-held"
          onClick={onRelease}
          disabled={releasing || !onRelease}
          title={`send ${heldCount === 1 ? "the generation" : `all ${heldCount} generations`} waiting to generate, in every project, to ComfyUI now — oldest first`}
        >
          {LATER_GLYPH} {heldCount}
          {!phone && " later"}
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
          title={veil.revealed ? "unlocked — tap to hide this project's pictures again; they hide on their own when you leave, press Generate, or after 20 s idle" : "locked — tap to show this project's pictures on this page"}
        >
          <VeilGlyph open={veil.revealed} />
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
            ComfyUI is connected and generates normally. The agent CLI is not running — start it on the machine and this becomes <code>✦ agent</code>.
          </span>
        </div>
      )}
    </div>
  );
}

/** The panel door: a frame with its right-hand column, filled while the column is open. */
function PanelGlyph({ open }: { open: boolean }) {
  return (
    <svg viewBox="0 0 20 16" width="18" height="15" aria-hidden="true">
      <rect x="1" y="1" width="18" height="14" rx="2.5" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <line x1="12.5" y1="1.5" x2="12.5" y2="14.5" stroke="currentColor" strokeWidth="1.6" />
      {open && <rect x="12.5" y="1.8" width="5.7" height="12.4" rx="1.6" fill="currentColor" />}
    </svg>
  );
}
