import { Link, Navigate, useParams } from "react-router-dom";
import { errorText, liveJobsFor, useCommand, useHeld, useLiveJobs, useProject } from "../api/client";
import { LATER_GLYPH } from "../components/LaterToggle";
import { Crumbs } from "../components/Crumbs";
import { BinFilterRow } from "../components/BinFilterRow";
import { LibraryTabs } from "../components/LibraryTabs";
import { useToast } from "../components/Toast";
import { useScrollMemory } from "../components/useScrollMemory";
import { useBinFilter } from "../layout/useBinFilter";
import { useCreateBin } from "../layout/useCreateBin";
import { useCreateSequence } from "../layout/useCreateSequence";
import { useDeleteBin } from "../layout/useDeleteBin";
import { useDeleteSequence } from "../layout/useDeleteSequence";
import { stored, useLibraryTab } from "../layout/useLibraryTab";
import { SequenceCards } from "../components/SequenceCards";
import { MediaThumb } from "../components/MediaThumb";
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
  const createSeq = useCreateSequence(slug);
  const deleteSeq = useDeleteSequence(slug, project.data?.bins);
  const [tab, setTab] = useLibraryTab(slug);
  const filter = useBinFilter(project.data?.bins, project.data?.tags, slug);
  // live renders per bin, from the event-fed list
  const live = useLiveJobs(slug);
  const rendering = (binId: string) => liveJobsFor(binId, live.data, undefined).length;
  const heldAll = useHeld(slug);
  const saved = (binId: string) => (heldAll.data ?? []).filter((g) => g.bin === binId).length;
  const command = useCommand(slug);
  const toast = useToast();

  if (wide) {
    // the sidebar is the explorer: land on the first sequence when that tab is up, else the most
    // recently active bin
    const firstSeq = (project.data?.sequences ?? [])[0];
    if (project.data && firstSeq && stored(slug) === "sequences") {
      return <Navigate to={`/p/${encodeURIComponent(project.data.slug)}/s/${encodeURIComponent(firstSeq.id)}`} replace />;
    }
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
          <Crumbs
            items={[
              { label: "Projects", to: "/", title: "every project" },
              { label: project.data?.name ?? slug ?? "", rename: { onSave: renameProject, ariaLabel: "Project name", disabled: !project.data } },
            ]}
          />
          <span className="spacer" />
          <Link to={`/p/${encodeURIComponent(slug ?? "")}/settings`} className="gear" title="project settings" aria-label="Project settings">
            ⚙
          </Link>
        </header>
        <div className="screen-body" ref={bodyRef}>
          {project.isLoading && <div className="hint">loading…</div>}
          {project.error && <div className="hint">could not load this project</div>}
          {project.data && <LibraryTabs tab={tab} onTab={setTab} bins={project.data.bins.length} sequences={(project.data.sequences ?? []).length} />}
          {project.data && tab === "sequences" && <SequenceCards slug={project.data.slug} sequences={project.data.sequences ?? []} onDelete={deleteSeq.ask} />}
          {project.data && tab === "bins" && project.data.bins.length === 0 && (
            <div className="blank-project">
              <b>Blank project</b>
              <div className="hint">
                {project.data.configured
                  ? "New bins inherit the project's canvas, workflows and style (⚙)."
                  : "Not configured yet — the agent sets it up from the brief, or open ⚙ to set canvas and workflows."}
              </div>
            </div>
          )}
          {tab === "bins" && project.data && project.data.bins.length > 0 && <BinFilterRow tags={project.data.tags} filter={filter} />}
          {tab === "bins" && project.data && project.data.bins.length > 0 && filter.shown.length === 0 && <div className="hint">nothing matches that filter</div>}
          {tab === "bins" && filter.shown.map((b) => (
            <Link key={b.id} to={`/p/${encodeURIComponent(project.data!.slug)}/b/${encodeURIComponent(b.id)}`} className="card shot-row">
              <MediaThumb slug={project.data!.slug} id={b.selected_media?.id} type={b.selected_media?.type} duration={b.selected_media?.duration_s} size={56} passive />
              <div className="shot-row-text">
                <b className="shot-row-name">
                  {b.shot != null && <span className="shot-no">{b.shot}</span>}
                  {b.name}
                </b>
                <div className="meta">{binMeta(b)}</div>
              </div>
              {rendering(b.id) > 0 && <span className="row-live" role="status" aria-label="generating" title={`${rendering(b.id)} generating`} />}
              {saved(b.id) > 0 && (
                <span className="row-held" title={`${saved(b.id)} waiting to generate`}>
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
          {tab === "bins" ? (
            <button
              type="button"
              className="btn-dashed"
              disabled={!project.data || createBin.pending}
              onClick={() => createBin.create({ workflow: project.data?.defaults.video_workflow || undefined })}
            >
              {createBin.pending ? "creating…" : "＋ New bin"}
            </button>
          ) : (
            <button type="button" className="btn-dashed" disabled={!project.data || createSeq.pending} onClick={createSeq.create} title="an ordered list of bins that plays each bin's selected take and exports to one mp4">
              {createSeq.pending ? "creating…" : "＋ New sequence"}
            </button>
          )}
        </footer>
        {deleteBin.sheet}
        {deleteSeq.sheet}
      </div>
    </Shell>
  );
}
