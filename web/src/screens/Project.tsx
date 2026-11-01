import { Link, Navigate, useParams } from "react-router-dom";
import { errorText, liveJobsFor, useCommand, useHeld, useLiveJobs, useProject } from "../api/client";
import { LATER_GLYPH } from "../components/LaterToggle";
import { BackLink } from "../components/BackLink";
import { BinFilterRow } from "../components/BinFilterRow";
import { EditableTitle } from "../components/EditableTitle";
import { useToast } from "../components/Toast";
import { useScrollMemory } from "../components/useScrollMemory";
import { useBinFilter } from "../layout/useBinFilter";
import { useCreateBin } from "../layout/useCreateBin";
import { useDeleteBin } from "../layout/useDeleteBin";
import { MediaThumb } from "../components/MediaThumb";
import { Tag } from "../components/Tag";
import { Shell } from "../layout/Shell";
import { useBreakpoint } from "../layout/useBreakpoint";
import type { BinCard } from "../api/types";

export function workflowStem(name: string): string {
  return name.replace(/\.json$/i, "");
}

export function binMeta(b: BinCard): string {
  const n = (b.media ?? []).length;
  const media = `${n} media`;
  const stem = workflowStem(b.workflow) || "no workflow";
  if (b.kind === "video") {
    const len = b.length_s != null ? `${Number(b.length_s)}s` : "-";
    return `${stem} · ${len} · ${media}`;
  }
  return `${stem} · ${media}`;
}

export function ProjectRoute() {
  const { slug } = useParams();
  const { wide } = useBreakpoint();
  const project = useProject(slug);
  const bodyRef = useScrollMemory<HTMLDivElement>(!!project.data);
  const createBin = useCreateBin(slug, project.data?.bins);
  const deleteBin = useDeleteBin(slug, project.data?.bins);
  const filter = useBinFilter(project.data?.bins, project.data?.tags);
  // live renders per bin, from the event-fed list
  const live = useLiveJobs(slug);
  const rendering = (binId: string) => liveJobsFor(binId, live.data, undefined).length;
  const heldAll = useHeld(slug);
  const saved = (binId: string) => (heldAll.data ?? []).filter((g) => g.bin === binId).length;
  const command = useCommand(slug);
  const toast = useToast();

  if (wide) {
    // the sidebar is the explorer: land on the most recently active bin
    if (project.data && project.data.bins.length > 0) {
      return <Navigate to={`/p/${encodeURIComponent(project.data.slug)}/b/${encodeURIComponent(project.data.bins[0].id)}`} replace />;
    }
    return (
      <Shell slug={slug}>
        <div className="canvas-empty">
          {project.isLoading ? (
            "loading…"
          ) : project.error ? (
            `could not load ${slug}`
          ) : (
            <div className="blank-project">
              <b>Blank project</b>
              <div className="hint">
                {project.data?.configured
                  ? "New bins inherit the project's canvas, workflows and style. Start one with ＋ New bin."
                  : "Not configured yet — the agent sets it up from the brief, or open settings to set canvas and workflows."}
              </div>
            </div>
          )}
        </div>
      </Shell>
    );
  }

  const renameProject = (name: string) =>
    command.mutate({ name: "set_project_settings", body: { project_name: name } }, { onError: (e) => toast(errorText(e)) });
  return (
    <Shell slug={slug}>
      <div className="screen">
        <header className="screen-head">
          <BackLink to="/" label="Back to projects" />
          <EditableTitle className="screen-title" value={project.data?.name ?? slug ?? ""} onSave={renameProject} ariaLabel="Project name" disabled={!project.data} />
          <span className="spacer" />
          <Link to={`/p/${encodeURIComponent(slug ?? "")}/settings`} className="gear" title="project settings" aria-label="Project settings">
            ⚙
          </Link>
          <Tag>PROJECT</Tag>
        </header>
        <div className="screen-body" ref={bodyRef}>
          {project.isLoading && <div className="hint">loading…</div>}
          {project.error && <div className="hint">could not load this project</div>}
          {project.data && project.data.bins.length === 0 && (
            <div className="blank-project">
              <b>Blank project</b>
              <div className="hint">
                {project.data.configured
                  ? "New bins inherit the project's canvas, workflows and style (⚙)."
                  : "Not configured yet — the agent sets it up from the brief, or open ⚙ to set canvas and workflows."}
              </div>
            </div>
          )}
          {project.data && project.data.bins.length > 0 && <BinFilterRow tags={project.data.tags} filter={filter} />}
          {project.data && project.data.bins.length > 0 && filter.shown.length === 0 && <div className="hint">nothing matches that filter</div>}
          {filter.shown.map((b) => (
            <Link key={b.id} to={`/p/${encodeURIComponent(project.data!.slug)}/b/${encodeURIComponent(b.id)}`} className="card shot-row">
              <MediaThumb slug={project.data!.slug} id={b.selected_media?.id} type={b.selected_media?.type} duration={b.selected_media?.duration_s} size={56} passive />
              <div className="shot-row-text">
                <b className="shot-row-name">{b.name}</b>
                <div className="meta">{binMeta(b)}</div>
              </div>
              {rendering(b.id) > 0 && <span className="row-live" role="status" aria-label="generating" title={`${rendering(b.id)} generating`} />}
              {saved(b.id) > 0 && (
                <span className="row-held" title={`${saved(b.id)} saved for later`}>
                  {LATER_GLYPH} {saved(b.id)}
                </span>
              )}
              {(b.unseen ?? []).length > 0 && (
                <span className="row-new" title="new takes to look at">
                  {(b.unseen ?? []).length}
                </span>
              )}
              {(b.tags ?? []).length > 0 && <span className="chip chip-kind">{(b.tags ?? []).join(" · ")}</span>}
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
        </div>
        <footer className="screen-foot">
          <button
            type="button"
            className="btn-dashed"
            disabled={!project.data || createBin.pending}
            onClick={() => createBin.create({ workflow: project.data?.defaults.video_workflow || undefined })}
          >
            {createBin.pending ? "creating…" : "＋ New bin"}
          </button>
        </footer>
        {deleteBin.sheet}
      </div>
    </Shell>
  );
}
