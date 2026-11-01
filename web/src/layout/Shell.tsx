import { useEffect, type ReactNode } from "react";
import { errorText, useAgentActivity, useAgentStatus, useCommand, useHeld, useLiveJobs, useProject, useProjectEvents, useProjects, useReleaseHeld } from "../api/client";
import type { SessionMode } from "../api/types";
import { applyTitle, setTitleScope } from "./tabTitle";
import { MediaViewer } from "../components/MediaViewer";
import { useToast } from "../components/Toast";
import { AgentPanel } from "./AgentPanel";
import { useAgentUi } from "./AgentUi";
import { readLastMode, writeLastMode } from "./modes";
import { Sidebar } from "./Sidebar";
import { SystemBar } from "./SystemBar";
import { useBreakpoint } from "./useBreakpoint";
import { useSessionPanel } from "./useSessionPanel";
import { VeilProvider, useVeilState } from "./veil";

interface Props {
  slug?: string;
  binId?: string;
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
export function Shell({ slug, binId, children }: Props) {
  const { wide, xwide } = useBreakpoint();
  const projects = useProjects();
  const project = useProject(slug);
  useProjectEvents(slug);
  const activity = useAgentActivity(slug);
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
  // the saved group: generations held for later, released from the bar as one gesture
  const held = useHeld(slug);
  const release = useReleaseHeld(slug);
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
    command.mutate(
      { name: "start_session", body: { notes: null, refs_bin: null, mode } },
      {
        onSuccess: () => {
          writeLastMode(mode);
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

  return (
    <VeilProvider value={veil}>
      <div className={`shell ${veil.veiled ? "is-veiled" : ""} ${!wide ? "is-phone" : ""}`}>
        <SystemBar
          slug={slug}
          project={project.data}
          projects={projects.data}
          liveJobs={liveJobs.data}
          activity={activity.data ?? null}
          agentAvailable={agentStatus.data?.available}
          panelOpen={panelOpen}
          phone={!wide}
          starting={command.isPending}
          heldCount={held.data?.length ?? 0}
          releasing={release.isPending}
          onRelease={releaseHeld}
          onTogglePanel={() => setPanel(!panelOpen)}
          onStart={startSession}
          onOpenTranscript={() => setPanel(true)}
          veil={veil}
        />
        <div className="shell-body">
          {wide && <Sidebar projects={projects.data ?? []} project={project.data} activeBin={binId} />}
          <main className="canvas">{children}</main>
          {railMounted && (
            <aside className={`rail ${railOpen ? "is-open" : ""}`} aria-hidden={!railOpen}>
              <div className="rail-inner">
                <AgentPanel slug={slug!} project={project.data} binId={binId} onClose={() => ui.setRailOpen(false)} onSetMode={setMode} />
              </div>
            </aside>
          )}
          {pushOpen && (
            <div className="panel-push">
              <AgentPanel slug={slug!} project={project.data} binId={binId} onSetMode={setMode} />
            </div>
          )}
        </div>
        <MediaViewer />
      </div>
    </VeilProvider>
  );
}
