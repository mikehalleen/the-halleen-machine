import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { errorText, postJson, useCommand, useCreateProject, useDeleteProject, useProject, useWorkflows } from "../api/client";
import type { CommandResultOut, ProjectDetail, ProjectSettingsBody, WorkflowInfo } from "../api/types";
import { AutoTextarea } from "../components/AutoTextarea";
import { BackLink } from "../components/BackLink";
import { ConfirmSheet } from "../components/ConfirmSheet";
import { Tag } from "../components/Tag";
import { useToast } from "../components/Toast";
import { canGoBack } from "../layout/history";
import { notificationState, sendTestNotification } from "../layout/notify";
import { Shell } from "../layout/Shell";
import { useBreakpoint } from "../layout/useBreakpoint";

const PRESETS = [
  { label: "9:16 · 720×1280", w: 720, h: 1280 },
  { label: "16:9 · 1280×720", w: 1280, h: 720 },
  { label: "1:1 · 1024×1024", w: 1024, h: 1024 },
  { label: "2:3 · 832×1216", w: 832, h: 1216 },
  { label: "3:2 · 1152×768", w: 1152, h: 768 },
  { label: "2:3 · 768×1152", w: 768, h: 1152 },
];

/** The nearest multiple of 16, never below 16. */
const snap16 = (n: number) => Math.max(16, Math.round(n / 16) * 16);

/** The canvas drawn to scale inside a fixed box, so a size reads as a shape before it is saved. */
function CanvasShape({ w, h }: { w: number; h: number }) {
  const box = 120;
  const scale = box / Math.max(w, h, 1);
  const width = Math.max(8, Math.round(w * scale));
  const height = Math.max(8, Math.round(h * scale));
  const orientation = w > h ? "landscape" : w < h ? "portrait" : "square";
  return (
    <div className="canvas-shape-box" style={{ height: box }} aria-label={`${w} by ${h}, ${orientation}`}>
      <div className="canvas-shape" style={{ width, height }}>
        <span>
          {w}×{h}
        </span>
        <span className="canvas-shape-sub">{orientation}</span>
      </div>
    </div>
  );
}

/** One side of the canvas: a field that commits a positive whole number on blur or Enter and
 * puts the last good value back otherwise. Typing is free in between, so clearing the field
 * to retype it never turns the canvas into 0×0. */
function SideField({ value, onCommit, label }: { value: number; onCommit: (n: number) => void; label: string }) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => {
    setDraft(String(value));
  }, [value]);
  const commit = () => {
    const n = Math.round(Number(draft));
    if (!Number.isFinite(n) || n <= 0) {
      setDraft(String(value));
      return;
    }
    if (n !== value) onCommit(n);
    else setDraft(String(n));
  };
  return (
    <input
      className="size-input"
      type="text"
      inputMode="numeric"
      value={draft}
      aria-label={label}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
        if (e.key === "Escape") {
          setDraft(String(value));
          e.currentTarget.blur();
        }
      }}
    />
  );
}

export const workflowLabel = (name: string) => name.replace(/\.json$/i, "");

interface Fields {
  name: string;
  w: number;
  h: number;
  imgWf: string;
  vidWf: string;
  imgP: string;
  vidP: string;
  imgN: string;
  vidN: string;
  private: boolean;
}

const EMPTY: Fields = { name: "", w: 720, h: 1280, imgWf: "", vidWf: "", imgP: "", vidP: "", imgN: "", vidN: "", private: false };

function fromProject(p: ProjectDetail): Fields {
  const d = p.defaults;
  return {
    name: p.name,
    w: d.width ?? 720,
    h: d.height ?? 1280,
    imgWf: d.image_workflow ?? "",
    vidWf: d.video_workflow ?? "",
    imgP: d.image_prompt ?? "",
    vidP: d.video_prompt ?? "",
    imgN: d.image_negative ?? "",
    vidN: d.video_negative ?? "",
    private: p.private ?? false,
  };
}

function settingsOf(f: Fields): ProjectSettingsBody {
  return {
    width: f.w,
    height: f.h,
    image_workflow: f.imgWf,
    video_workflow: f.vidWf,
    image_prompt: f.imgP.trim(),
    video_prompt: f.vidP.trim(),
    image_negative: f.imgN.trim(),
    video_negative: f.vidN.trim(),
    private: f.private,
  };
}

function notifyHint(): string {
  switch (notificationState()) {
    case "unsupported":
      return "this browser has no notifications";
    case "insecure":
      return "needs https (or localhost) — the browser refuses on plain http";
    case "denied":
      return "blocked — allow notifications for this site in the browser's site settings";
    case "granted":
      return "allowed — a finished generation or a card needing your ok alerts you when this tab is not in front";
    default:
      return "not asked yet — the first Generate asks";
  }
}

export function NewProjectRoute() {
  return <ProjectForm mode="new" />;
}

export function ProjectSettingsRoute() {
  const { slug } = useParams();
  return <ProjectForm mode="edit" slug={slug} />;
}

/**
 * Prototype "New project" and "Project settings": one form. New mode offers the agent path
 * (send a brief, the agent configures the project) above the manual fields; edit mode is
 * prefilled, warns that changes apply to new generations only, and returns to the project.
 */
function ProjectForm({ mode, slug }: { mode: "new" | "edit"; slug?: string }) {
  const navigate = useNavigate();
  const toast = useToast();
  const { xwide } = useBreakpoint();
  const project = useProject(mode === "edit" ? slug : undefined);
  const workflows = useWorkflows();
  const create = useCreateProject();
  const command = useCommand(slug);
  const [f, setF] = useState<Fields>(EMPTY);
  const [brief, setBrief] = useState("");
  const [globOpen, setGlobOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const deleteProject = useDeleteProject();
  const [filled, setFilled] = useState(false);
  const set = <K extends keyof Fields>(key: K, value: Fields[K]) => setF((prev) => ({ ...prev, [key]: value }));

  // prefill once in edit mode; in new mode start on each kind's configured default
  useEffect(() => {
    if (filled) return;
    if (mode === "edit") {
      if (!project.data) return;
      const next = fromProject(project.data);
      setF(next);
      setGlobOpen(!!(next.imgP || next.vidP || next.imgN || next.vidN));
      setFilled(true);
    } else if (workflows.data) {
      const pick = (kind: string) => workflows.data!.filter((w) => w.kind === kind).find((w) => w.is_default) ?? workflows.data!.find((w) => w.kind === kind);
      const img = pick("image");
      const vid = pick("video");
      setF((prev) => ({ ...prev, imgWf: prev.imgWf || img?.name || "", vidWf: prev.vidWf || vid?.name || "" }));
      setFilled(true);
    }
  }, [mode, project.data, workflows.data, filled]);

  const images = (workflows.data ?? []).filter((w) => w.kind === "image");
  const videos = (workflows.data ?? []).filter((w) => w.kind === "video");
  const promptCount = [f.imgP, f.vidP, f.imgN, f.vidN].filter((x) => x.trim()).length;
  const busy = create.isPending || command.isPending;
  const sessionOn = project.data?.agent_session?.status === "live" || project.data?.agent_session?.status === "waiting";

  const goBack = () => {
    if (canGoBack()) navigate(-1);
    else navigate(mode === "edit" && slug ? `/p/${encodeURIComponent(slug)}` : "/", { replace: true });
  };

  const save = () => {
    if (!f.name.trim()) {
      toast("name the project first");
      return;
    }
    if (mode === "new") {
      create.mutate(
        { name: f.name.trim(), settings: settingsOf(f) },
        {
          onSuccess: (detail) => {
            toast("project created — blank canvas ready");
            navigate(`/p/${encodeURIComponent(detail.slug)}`, { replace: true });
          },
          onError: (e) => toast(errorText(e)),
        },
      );
      return;
    }
    command.mutate(
      { name: "set_project_settings", body: { project_name: f.name.trim(), ...settingsOf(f) } },
      {
        onSuccess: () => {
          toast("saved — applies to new generations");
          goBack();
        },
        onError: (e) => toast(errorText(e)),
      },
    );
  };

  const agentCreate = () => {
    if (!brief.trim()) {
      toast("write a brief first");
      return;
    }
    if (!f.name.trim()) {
      toast("name the project first");
      return;
    }
    create.mutate(
      { name: f.name.trim(), brief: brief.trim() },
      {
        onSuccess: async (detail) => {
          try {
            await postJson<CommandResultOut>(`/api/projects/${encodeURIComponent(detail.slug)}/commands/start_session`, {});
          } catch (e) {
            toast(errorText(e));
          }
          toast("brief sent — agent connecting");
          navigate(`/p/${encodeURIComponent(detail.slug)}${xwide ? "" : "?panel=session"}`, { replace: true });
        },
        onError: (e) => toast(errorText(e)),
      },
    );
  };

  const title = mode === "edit" ? "Project settings" : "New project";
  return (
    <Shell slug={mode === "edit" ? slug : undefined}>
      <div className="screen">
        <header className="screen-head">
          <BackLink to={mode === "edit" && slug ? `/p/${encodeURIComponent(slug)}` : "/"} label="Back" />
          <b>{title}</b>
          <span className="spacer" />
          <Tag>SETUP</Tag>
        </header>
        <div className="screen-body form-body">
          {mode === "edit" && project.isLoading && <div className="hint">loading…</div>}
          {mode === "edit" && project.error && <div className="hint">could not load this project</div>}

          {mode === "new" && !sessionOn && (
            <>
              <div className="agent-block">
                <div className="row">
                  <span className="dot is-live" />
                  <b>Let the agent create it</b>
                </div>
                <AutoTextarea
                  className="field-input"
                  value={brief}
                  onChange={setBrief}
                  minRows={3}
                  placeholder="Brief — the agent sets everything below from it… e.g. vertical 9:16 rail promo, i2v_base for video, dawn light + 35mm grain style, two shots of mara"
                  aria-label="Brief"
                />
                <button type="button" className={`btn-primary btn-block ${brief.trim() && f.name.trim() ? "" : "is-muted"}`} onClick={agentCreate} disabled={busy}>
                  Send brief · agent creates project
                </button>
                <div className="hint is-center">size, workflow defaults, and style notes in the brief become the settings below</div>
              </div>
              <div className="or-divider">
                <span />
                <span className="tag">OR SET UP MANUALLY</span>
                <span />
              </div>
            </>
          )}

          <label className="field">
            <span className="tag">NAME</span>
            <input className="field-input" value={f.name} onChange={(e) => set("name", e.target.value)} placeholder="untitled project" />
          </label>

          <div className="field">
            <span className="tag">CANVAS</span>
            <div className="row wrap chips">
              {PRESETS.map((p) => (
                <button
                  key={p.label}
                  type="button"
                  className={`preset-chip ${f.w === p.w && f.h === p.h ? "is-on" : ""}`}
                  onClick={() => {
                    set("w", p.w);
                    set("h", p.h);
                  }}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <div className="row size-row">
              <SideField value={f.w} onCommit={(n) => set("w", n)} label="Canvas width" />
              <span className="size-x">×</span>
              <SideField value={f.h} onCommit={(n) => set("h", n)} label="Canvas height" />
              <button
                type="button"
                className="chip"
                title="swap width and height"
                aria-label="Swap width and height"
                onClick={() => {
                  const w = f.w;
                  set("w", f.h);
                  set("h", w);
                }}
              >
                ⇄
              </button>
              <button
                type="button"
                className="chip"
                title="round both sides to the nearest multiple of 16"
                disabled={f.w % 16 === 0 && f.h % 16 === 0}
                onClick={() => {
                  set("w", snap16(f.w));
                  set("h", snap16(f.h));
                }}
              >
                snap to 16
              </button>
            </div>
            <div className="hint">any size — most models want multiples of 16 · ⇄ flips portrait and landscape</div>
            <CanvasShape w={f.w} h={f.h} />
          </div>

          <div className="field">
            <span className="tag">DEFAULT WORKFLOWS</span>
            <WorkflowSelect label="image" value={f.imgWf} options={images} onChange={(v) => set("imgWf", v)} />
            <WorkflowSelect label="video" value={f.vidWf} options={videos} onChange={(v) => set("vidWf", v)} />
            <div className="hint">every new bin starts on these — each bin can still switch its own workflow</div>
          </div>

          <div className="glob">
            <button type="button" className="glob-head" onClick={() => setGlobOpen((o) => !o)} aria-expanded={globOpen}>
              <span className="tag">GLOBAL PROMPTS</span>
              <span className="hint">{promptCount ? `${promptCount} set` : "none set"}</span>
              <span className="spacer" />
              <span className="glob-caret">{globOpen ? "⌄" : "›"}</span>
            </button>
            {globOpen && (
              <div className="glob-body">
                <label className="field">
                  <span className="tag">GLOBAL IMAGE PROMPT</span>
                  <AutoTextarea className="field-input veil-text" value={f.imgP} onChange={(v) => set("imgP", v)} placeholder="appended to every image prompt…" />
                </label>
                <label className="field">
                  <span className="tag">GLOBAL VIDEO PROMPT</span>
                  <AutoTextarea className="field-input veil-text" value={f.vidP} onChange={(v) => set("vidP", v)} placeholder="appended to every video prompt…" />
                </label>
                <label className="field">
                  <span className="tag">GLOBAL IMAGE NEGATIVE</span>
                  <AutoTextarea className="field-input is-negative veil-text" value={f.imgN} onChange={(v) => set("imgN", v)} placeholder="merged into every image negative…" />
                </label>
                <label className="field">
                  <span className="tag">GLOBAL VIDEO NEGATIVE</span>
                  <AutoTextarea className="field-input is-negative veil-text" value={f.vidN} onChange={(v) => set("vidN", v)} placeholder="merged into every video negative…" />
                </label>
                <div className="hint">prompts are appended after each bin's prompt; negatives merge with per-bin negatives</div>
              </div>
            )}
          </div>

          {mode === "edit" && (
            <div className="field">
              <span className="tag">NOTIFICATIONS</span>
              <div className="row chips">
                <button type="button" className="chip" onClick={() => void sendTestNotification().then((r) => toast(`test: ${r}`))}>
                  send a test
                </button>
                <span className="hint">{notifyHint()}</span>
              </div>
            </div>
          )}

          <div className="field">
            <span className="tag">PRIVATE</span>
            <div className="row chips">
              <button type="button" className={`chip ${f.private ? "is-on" : ""}`} aria-pressed={f.private} onClick={() => set("private", !f.private)}>
                {f.private ? "on" : "off"}
              </button>
              <span className="hint">no cover on the project list; the agent cannot see it</span>
            </div>
          </div>

          {mode === "edit" && project.data && (
            <div className="field danger-zone">
              <span className="tag">DELETE PROJECT</span>
              <div className="row">
                <button type="button" className="btn-outline btn-outline-danger" onClick={() => setDeleting(true)} disabled={busy}>
                  Delete “{project.data.name}”…
                </button>
              </div>
              <span className="hint">every bin, its media records, the feed and the agent history go; generated files stay in ComfyUI's output folder</span>
            </div>
          )}
        </div>
        <footer className="screen-foot form-foot">
          <button type="button" className="btn-outline" onClick={goBack} disabled={busy}>
            Cancel
          </button>
          <button type="button" className={`btn-primary btn-wide ${f.name.trim() ? "" : "is-muted"}`} onClick={save} disabled={busy}>
            {mode === "edit" ? "Save changes" : "Create project"}
          </button>
        </footer>
        {deleting && project.data && (
          <ConfirmSheet
            title={`Delete ${project.data.name}?`}
            detail={`${project.data.bins.length} ${project.data.bins.length === 1 ? "bin" : "bins"}, every upload and clip, and the agent history go with it. Generated files stay in ComfyUI's output folder. This cannot be undone.`}
            confirmLabel="Delete project"
            onConfirm={() => {
              setDeleting(false);
              deleteProject.mutate(slug!, {
                onSuccess: () => {
                  toast(`deleted ${project.data?.name ?? slug}`);
                  navigate("/", { replace: true });
                },
                onError: (e) => toast(errorText(e)),
              });
            }}
            onCancel={() => setDeleting(false)}
          />
        )}
      </div>
    </Shell>
  );
}

function WorkflowSelect({ label, value, options, onChange }: { label: string; value: string; options: WorkflowInfo[]; onChange: (v: string) => void }) {
  const known = options.some((w) => w.name === value);
  return (
    <label className="row wf-row">
      <span className="wf-label">{label}</span>
      <select className="field-select" value={value} onChange={(e) => onChange(e.target.value)} aria-label={`default ${label} workflow`}>
        {!known && value && <option value={value}>{workflowLabel(value)}</option>}
        {options.map((w) => (
          <option key={w.name} value={w.name}>
            {workflowLabel(w.name)}
          </option>
        ))}
      </select>
    </label>
  );
}
