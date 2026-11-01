import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useProjects } from "../api/client";
import { Crumbs } from "../components/Crumbs";
import { MediaThumb } from "../components/MediaThumb";
import { useScrollMemory } from "../components/useScrollMemory";
import { Shell } from "../layout/Shell";
import { useBreakpoint } from "../layout/useBreakpoint";
import { projectAttention } from "../layout/SystemBar";
import type { ProjectCard } from "../api/types";

/** How many projects Home shows before the fold. */
const SHOWN = 5;

export function projectMeta(p: ProjectCard): string {
  const bins = `${p.bin_count} ${p.bin_count === 1 ? "bin" : "bins"}`;
  if (p.canvas) return `${p.canvas.replace("x", "×")} · ${bins}`;
  const v = p.video_count;
  const i = p.image_count;
  return `${v} ${v === 1 ? "video" : "videos"} · ${i} ${i === 1 ? "image" : "images"}`;
}

function ProjectRow({ p, toAgent }: { p: ProjectCard; toAgent: boolean }) {
  // the same facts the bar counts, said here per project so `2 sessions live · 1 needs you` and
  // a render elsewhere can each be traced to a row: the bar's own signs, in its own order --
  // the cyan render dot, the green session dot, the amber count of what it owes you
  const { live, needsYou } = projectAttention(p);
  const rendering = p.generating;
  const state = [
    rendering ? `${rendering} ${rendering === 1 ? "generation" : "generations"} running here` : "",
    live ? "an agent session is live here" : "",
    needsYou ? `${needsYou} waiting for you` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <Link to={`/p/${encodeURIComponent(p.slug)}${toAgent && needsYou > 0 ? "?panel=session" : ""}`} className="project-row">
      <MediaThumb slug={p.slug} id={p.hero?.id} type={p.hero?.type} size={52} ratio={1.5} passive dashed={!p.hero} />
      <div className="project-row-text">
        <div className="project-row-name">{p.name}</div>
        <div className="project-row-meta">
          {projectMeta(p)}
          {p.private && " · private"}
        </div>
      </div>
      {(rendering > 0 || live > 0 || needsYou > 0) && (
        <span className="project-row-state" title={state} aria-label={state}>
          {rendering > 0 && (
            <span className="project-row-render" aria-hidden="true">
              <span className="dot is-render" />
              {rendering > 1 && rendering}
            </span>
          )}
          {live > 0 && <span className="dot is-working" aria-hidden="true" />}
          {needsYou > 0 && (
            <span className="badge-amber is-small" aria-hidden="true">
              {needsYou}
            </span>
          )}
        </span>
      )}
      <span className="project-row-chevron" aria-hidden="true">
        ›
      </span>
    </Link>
  );
}

/** The project list, the same screen at every width: wide gets it centred in the canvas beside
 * a sidebar with no project picked. The rows are a list of projects and nothing more -- every
 * bit of server state (renders, sessions, what needs you) is in the bar above. Nothing
 * auto-opens a project: Home is where a delete, a back link, and a fresh launch all land.
 *
 * The glanceable top is the first few public projects; the rest, and every private project
 * wherever it sits, wait behind "more", which folds again on every visit. */
export function HomeRoute() {
  const projects = useProjects();
  const bodyRef = useScrollMemory<HTMLDivElement>(!!projects.data);
  const [more, setMore] = useState(false);
  // reached from the bar with the agent's push open (below 1120): a project with amber waiting
  // opens straight into its panel, so answering the next one is two taps
  const { xwide } = useBreakpoint();
  const fromAgent = !!(useLocation().state as { fromAgent?: boolean } | null)?.fromAgent;
  const toAgent = fromAgent && !xwide;

  // the server lists public projects first, newest first, then the private ones; the fold
  // keeps that order and only draws the line
  const all = projects.data ?? [];
  const shown = all.filter((p) => !p.private).slice(0, SHOWN);
  const folded = all.filter((p) => !shown.includes(p));

  return (
    <Shell>
      <div className="screen">
        <header className="screen-head">
          <Crumbs items={[{ label: "Projects" }]} />
          <span className="spacer" />
        </header>
        <div className="screen-body home-body" ref={bodyRef}>
          {projects.isLoading && <div className="hint">loading…</div>}
          {projects.error && <div className="hint">could not reach the server</div>}
          {shown.map((p) => (
            <ProjectRow key={p.slug} p={p} toAgent={toAgent} />
          ))}
          {folded.length > 0 && (
            <button type="button" className="home-more" onClick={() => setMore(!more)} aria-expanded={more}>
              {more ? "⌃ fewer" : `⌄ ${folded.length} more`}
            </button>
          )}
          {more && folded.map((p) => <ProjectRow key={p.slug} p={p} toAgent={toAgent} />)}
          {projects.data && (
            <Link to="/new" className="btn-dashed-block">
              ＋ New project
            </Link>
          )}
        </div>
      </div>
    </Shell>
  );
}
