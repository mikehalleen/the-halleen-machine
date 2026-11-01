import { useEffect, type ReactNode } from "react";
import { errorText, useAgentStatus, useCommand, useLiveJobs, useProject, useProjectEvents, useProjects, useReleaseHeld } from "../api/client";
import type { SessionMode } from "../api/types";
import { applyTitle, setTitleScope } from "./tabTitle";
import { MediaViewer } from "../components/MediaViewer";
import { useToast } from "../components/Toast";
import { AgentPanel } from "./AgentPanel";
import { useAgentUi } from "./AgentUi";
import { readLastDrafts, readLastMode, writeLastDrafts, writeLastMode } from "./modes";
import { PaneResizer, usePaneWidths } from "./PaneResizer";
import { Sidebar } from "./Sidebar";
import { SystemBar } from "./SystemBar";
import { useBreakpoint } from "./useBreakpoint";
import { useSessionPanel } from "./useSessionPanel";
import { VeilProvider, useVeilState } from "./veil";

interface Props {
  slug?: string;
  binId?: string;
  /** The sequence on screen, so the sidebar's card for it reads as active. */
  seqId?: string;
  children: ReactNode;
}

/**
 * The frame every screen renders inside:
 *   system bar (full width, always)
 *   [ sidebar >=760 | canvas | rail >=1120 (the agent panel, a push column) ]
 * Below 1120 the panel is a full-screen push over sidebar and canvas, kept in the URL so
 * back closes it; the bar stays above it. The panel exists only while there is a session to
 * show (live, waiting, or ended for its transcript); the bar's start control makes one.
 */
export function Shell({ slug, binId, seqId, children }: Props) {
  const { wide, xwide } = useBreakpoint();
  const projects = useProjects();
  const project = useProject(slug);
  useProjectEvents(slug);
  const liveJobs = useLiveJobs(slug);
  // the tab title carries the step counter (layout/tabTitle.ts); the stream and the fetches
  // write it directly, this only keeps it right across navigation and the first render
  useEffect(() => {
    setTitleScope(slug ?? null);
    applyTitle(slug ?? null, slug ? (liveJobs.data ?? []) : (projects.data ?? []).map((p) => p.generating_job));
  }, [slug, liveJobs.data, projects.data]);
  const agentStatus = useAgentStatus();
  const ui = useAgentUi();
  const panel = useSessionPanel();
  const command = useCommand(slug);
  const toast = useToast();
  // the saved group: generations held for later in every project, released from the bar as one
  // gesture, oldest first -- the GPU is one queue whichever project asked
  const heldCount = (projects.data ?? []).reduce((n, p) => n + (p.held ?? 0), 0);
  const release = useReleaseHeld();
  const releaseHeld = () =>
    release.mutate(undefined, {
      onSuccess: (jobs) => toast(jobs.length === 1 ? "1 sent to ComfyUI" : `${jobs.length} sent to ComfyUI`),
      onError: (e) => toast(errorText(e)),
    });

  const session = project.data?.agent_session ?? null;
  // a private project's pictures are veiled (layout/veil.tsx); the bar's pill lifts it on a phone
  const veil = useVeilState(!!project.data?.private);
  const hasSession = !!session && session.status !== "none";
  const railMounted = !!slug && xwide && hasSession;
  const railOpen = railMounted && ui.railOpen;
  const pushOpen = !!slug && !xwide && panel.open && hasSession;
  const panelOpen = railOpen || pushOpen;
  const panes = usePaneWidths();

  // panelOpen is orthogonal to the session: closing never touches it, ending never closes it
  const setPanel = (open: boolean) => {
    if (!slug) return;
    if (xwide) ui.setRailOpen(open);
    else if (open) panel.openPanel();
    else panel.closePanel();
  };

  // one click, last-used mode, no form: notes and reference media are the panel's afterwards
  const startSession = () => {
    if (!slug || command.isPending || project.data?.private) return; // the server refuses it anyway
    const mode = readLastMode(session?.mode);
    const drafts = readLastDrafts(session?.drafts);
    command.mutate(
      { name: "start_session", body: { notes: null, refs_bin: null, mode, drafts } },
      {
        onSuccess: () => {
          writeLastMode(mode);
          writeLastDrafts(drafts);
          setPanel(true);
        },
        onError: (e) => toast(errorText(e)),
      },
    );
  };
  const setMode = (mode: SessionMode) =>
    command.mutate(
      { name: "set_session_mode", body: { mode } },
      { onSuccess: () => writeLastMode(mode), onError: (e) => toast(errorText(e)) },
    );
  const setDrafts = (drafts: boolean) =>
    command.mutate(
      { name: "set_session_mode", body: { drafts } },
      { onSuccess: () => writeLastDrafts(drafts), onError: (e) => toast(errorText(e)) },
    );

  return (
    <VeilProvider value={veil}>
      <div className={`shell ${veil.veiled ? "is-veiled" : ""} ${!wide ? "is-phone" : ""}`}>
        <SystemBar
          slug={slug}
          project={project.data}
          projects={projects.data}
          liveJobs={liveJobs.data}
          agentAvailable={agentStatus.data?.available}
          panelOpen={panelOpen}
          phone={!wide}
          starting={command.isPending}
          heldCount={heldCount}
          releasing={release.isPending}
          onRelease={releaseHeld}
          onTogglePanel={() => setPanel(!panelOpen)}
          onStart={startSession}
          veil={veil}
        />
        <div className="shell-body" style={panes.style}>
          {wide && <Sidebar projects={projects.data ?? []} project={project.data} activeBin={binId} activeSequence={seqId} />}
          {wide && <PaneResizer pane="sidebar" onWidth={panes.set} />}
          <main className="canvas">{children}</main>
          {railOpen && <PaneResizer pane="rail" onWidth={panes.set} />}
          {railMounted && (
            <aside className={`rail ${railOpen ? "is-open" : ""}`} aria-hidden={!railOpen}>
              <div className="rail-inner">
                <AgentPanel slug={slug!} project={project.data} binId={binId} seqId={seqId} onClose={() => ui.setRailOpen(false)} onSetMode={setMode} onSetDrafts={setDrafts} />
              </div>
            </aside>
          )}
          {pushOpen && (
            <div className="panel-push">
              <AgentPanel slug={slug!} project={project.data} binId={binId} seqId={seqId} onSetMode={setMode} onSetDrafts={setDrafts} />
            </div>
          )}
        </div>
        <MediaViewer />
      </div>
    </VeilProvider>
  );
}
