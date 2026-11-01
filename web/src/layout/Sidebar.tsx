import { Link, useNavigate } from "react-router-dom";
import { liveJobsFor, useLiveJobs } from "../api/client";
import type { ProjectCard, ProjectDetail } from "../api/types";
import { BinFilterRow } from "../components/BinFilterRow";
import { MediaThumb } from "../components/MediaThumb";
import { useBinFilter } from "./useBinFilter";
import { useCreateBin } from "./useCreateBin";
import { useDeleteBin } from "./useDeleteBin";

interface Props {
  projects: ProjectCard[];
  project?: ProjectDetail | null;
  activeBin?: string;
}

/** Persistent left column at >= 760: project switcher, settings, and the library explorer —
 * every bin, newest first, filtered by tag and by name. The whole row opens the bin. The agent's
 * front door is the system bar, not here. */
export function Sidebar({ projects, project, activeBin }: Props) {
  const navigate = useNavigate();
  const createBin = useCreateBin(project?.slug, project?.bins);
  const deleteBin = useDeleteBin(project?.slug, project?.bins, activeBin);
  const filter = useBinFilter(project?.bins, project?.tags);
  // live renders per bin, from the event-fed list
  const live = useLiveJobs(project?.slug);
  const rendering = (binId: string) => liveJobsFor(binId, live.data, undefined).length;
  return (
    <aside className="sidebar">
      <div className="sidebar-head">
        <div className="row">
          <div className="tag">PROJECT</div>
          <span className="spacer" />
          <Link to="/new" className="link">
            ＋ new
          </Link>
        </div>
        <select
          className="sidebar-select"
          value={project?.slug ?? ""}
          onChange={(e) => navigate(`/p/${encodeURIComponent(e.target.value)}`)}
          aria-label="Project"
        >
          {!project && <option value="">pick a project</option>}
          {projects.map((p) => (
            <option key={p.slug} value={p.slug}>
              {p.name}
            </option>
          ))}
        </select>
        {project && (
          <div className="row sidebar-tools">
            <Link to={`/p/${encodeURIComponent(project.slug)}/settings`} className="gear" title="project settings" aria-label="Project settings">
              ⚙ settings
            </Link>
          </div>
        )}
      </div>
      {project && <BinFilterRow tags={project.tags} filter={filter} className="sidebar-filters" />}
      <div className="sidebar-list">
        {project &&
          filter.shown.map((b) => (
            <Link
              key={b.id}
              to={`/p/${encodeURIComponent(project.slug)}/b/${encodeURIComponent(b.id)}`}
              className={`sidebar-row ${b.id === activeBin ? "is-active" : ""}`}
            >
              <MediaThumb slug={project.slug} id={b.selected_media?.id} type={b.selected_media?.type} size={36} passive dashed={!b.selected_media} />
              <span className="sidebar-row-text">
                <span className="sidebar-row-name">{b.name}</span>
                {(b.tags ?? []).length > 0 && (
                  <span className="sidebar-row-kind" title={(b.tags ?? []).join(", ")}>
                    {(b.tags ?? []).join(" · ")}
                  </span>
                )}
              </span>
              {rendering(b.id) > 0 && <span className="row-live" role="status" aria-label="generating" title={`${rendering(b.id)} generating`} />}
              {(b.unseen ?? []).length > 0 && (
                <span className="row-new" title="new takes to look at">
                  {(b.unseen ?? []).length}
                </span>
              )}
              <button
                type="button"
                className="row-delete"
                title={`delete ${b.name}`}
                aria-label={`Delete ${b.name}`}
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  deleteBin.ask(b);
                }}
              >
                ✕
              </button>
            </Link>
          ))}
        {project && project.bins.length === 0 && <div className="hint">no bins yet</div>}
        {project && project.bins.length > 0 && filter.shown.length === 0 && <div className="hint">nothing matches that filter</div>}
      </div>
      {deleteBin.sheet}
      {project && (
        <div className="sidebar-foot">
          <button type="button" className="btn-dashed" disabled={createBin.pending} onClick={() => createBin.create({ workflow: project.defaults.video_workflow || undefined })}>
            {createBin.pending ? "creating…" : "＋ New bin"}
          </button>
        </div>
      )}
    </aside>
  );
}
