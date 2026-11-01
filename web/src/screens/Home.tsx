import { Link } from "react-router-dom";
import { useProjects } from "../api/client";
import { MediaThumb } from "../components/MediaThumb";
import { Tag } from "../components/Tag";
import { useScrollMemory } from "../components/useScrollMemory";
import { Shell } from "../layout/Shell";
import type { ProjectCard } from "../api/types";

export function projectMeta(p: ProjectCard): string {
  const bins = `${p.bin_count} ${p.bin_count === 1 ? "bin" : "bins"}`;
  if (p.canvas) return `${p.canvas.replace("x", "×")} · ${bins}`;
  const v = p.video_count;
  const i = p.image_count;
  return `${v} ${v === 1 ? "video" : "videos"} · ${i} ${i === 1 ? "image" : "images"}`;
}

/** The project list, the same screen at every width: wide gets it centred in the canvas beside
 * a sidebar with no project picked. The rows are a list of projects and nothing more -- every
 * bit of server state (renders, sessions, what needs you) is in the bar above. Nothing
 * auto-opens a project: Home is where a delete, a back link, and a fresh launch all land. */
export function HomeRoute() {
  const projects = useProjects();
  const bodyRef = useScrollMemory<HTMLDivElement>(!!projects.data);

  return (
    <Shell>
      <div className="screen">
        <header className="screen-head">
          <b>Projects</b>
          <span className="spacer" />
          <Tag>HOME</Tag>
        </header>
        <div className="screen-body home-body" ref={bodyRef}>
          {projects.isLoading && <div className="hint">loading…</div>}
          {projects.error && <div className="hint">could not reach the server</div>}
          {projects.data?.map((p) => (
            <Link key={p.slug} to={`/p/${encodeURIComponent(p.slug)}`} className="project-row">
              <MediaThumb slug={p.slug} id={p.hero?.id} type={p.hero?.type} size={52} ratio={1.5} passive dashed={!p.hero} />
              <div className="project-row-text">
                <div className="project-row-name">{p.name}</div>
                <div className="project-row-meta">{projectMeta(p)}</div>
              </div>
              <span className="project-row-chevron" aria-hidden="true">
                ›
              </span>
            </Link>
          ))}
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
