import { useEffect, useState, type ReactNode } from "react";
import { errorText, useEnhance, useEnhanceProgress, useEnhanceStatus, type EnhanceProgress } from "../api/client";
import type { EnhanceEngine, Kind } from "../api/types";
import { AutoTextarea } from "./AutoTextarea";
import { JobStatusLine, jobStepLabel } from "./JobProgress";
import { useToast } from "./Toast";

interface Props {
  /** The prompt card's own label; the pill sits at the other end of its row. */
  label: ReactNode;
  /** The prompt field itself; the sheet opens beneath it. */
  children: ReactNode;
  slug: string | undefined;
  kind: Kind;
  workflow: string;
  /** The prompt as it reads right now, draft included. */
  value: string;
  references: { handle: string; type: string }[];
  /** The rewrite lands here; the caller owns the field and commits it however it commits edits. */
  onReplace: (prompt: string) => void;
  /** A live agent session unlocks the agent engine as a choice. */
  agentLive: boolean;
  /** The bin this prompt belongs to, so the agent exchange in the feed can name it. */
  binId?: string;
  /** Names this prompt across navigation: an open sheet and a half-typed note survive a trip
   * to look at a reference and back. */
  storageKey: string;
}

interface Draft {
  open: boolean;
  note: string;
  /** An explicit pick; null means the default — the agent whenever a session is live, else local. */
  engine: EnhanceEngine | null;
}

const EMPTY: Draft = { open: false, note: "", engine: null };

function engineOf(p: { engine?: string } | null | undefined): string | null {
  return p?.engine ?? null;
}

function readDraft(key: string): Draft {
  try {
    const raw = sessionStorage.getItem(`thm.enhance.${key}`);
    return raw ? { ...EMPTY, ...(JSON.parse(raw) as Partial<Draft>) } : EMPTY;
  } catch {
    return EMPTY;
  }
}

function writeDraft(key: string, draft: Draft) {
  try {
    if (draft.open || draft.note) sessionStorage.setItem(`thm.enhance.${key}`, JSON.stringify(draft));
    else sessionStorage.removeItem(`thm.enhance.${key}`);
  } catch {
    /* storage unavailable: the sheet still works for this visit */
  }
}

/**
 * The `✦ Enhance` pill beside a prompt label and the sheet it opens: a note, an engine choice
 * when there is one, and Enhance / Close. The rewrite replaces the prompt and the sheet closes;
 * nothing is generated. One undo is kept in memory until the prompt is edited again.
 *
 * Hidden entirely when no engine can serve — no local enhancer workflow, ComfyUI down, no agent.
 */
export function EnhancePrompt({ label, children, slug, kind, workflow, value, references, onReplace, agentLive, storageKey, binId }: Props) {
  const status = useEnhanceStatus(slug);
  const enhance = useEnhance(slug);
  // the local engine's live phase and step count, while this request is in flight
  const progress = useEnhanceProgress(slug).data;
  const live =
    enhance.isPending && engineOf(progress) === "local" && progress?.status === "running"
      ? (progress as EnhanceProgress & { status: "running" })
      : null;
  // the agent engine's stage: queued (nobody has looked), seen (its watch listed the request),
  // working (it picked it up) -- so a request never looks unheard while the agent thinks
  const agentStage = enhance.isPending && engineOf(progress) === "agent" && ["queued", "seen", "working"].includes(progress?.status ?? "") ? progress : null;
  // short on the button; the full stage goes in its tooltip
  const agentLabel = agentStage ? (agentStage.status === "working" ? "rewriting…" : "waiting…") : null;
  const agentTitle = agentStage
    ? agentStage.status === "working"
      ? "the agent is rewriting the prompt"
      : agentStage.status === "seen"
        ? "the agent has seen the request and will start shortly"
        : "sent — waiting for the agent to notice"
    : undefined;
  const toast = useToast();
  const [draft, setDraft] = useState<Draft>(() => readDraft(storageKey));
  const { open, note, engine: picked } = draft;
  const setOpen = (open: boolean) => setDraft((d) => ({ ...d, open }));
  const setNote = (note: string) => setDraft((d) => ({ ...d, note }));
  const setEngine = (engine: EnhanceEngine) => setDraft((d) => ({ ...d, engine }));
  useEffect(() => writeDraft(storageKey, draft), [storageKey, draft]);
  /** `{ before, after }` of the last rewrite: undo restores `before` while the prompt still reads `after`. */
  const [undo, setUndo] = useState<{ before: string; after: string } | null>(null);

  const local = !!status.data?.local;
  const agent = agentLive && !!status.data?.agent;
  const available = local || agent;
  // a pick that can no longer serve (the session ended mid-edit) drops back to the default
  useEffect(() => {
    if ((picked === "agent" && !agent) || (picked === "local" && !local)) setDraft((d) => ({ ...d, engine: null }));
  }, [picked, agent, local]);
  const engine: EnhanceEngine = picked ?? (agent ? "agent" : "local");
  // any edit after a rewrite forfeits the undo — it is a one-shot, not a history
  useEffect(() => {
    if (undo && value !== undo.after) setUndo(null);
  }, [value, undo]);

  if (!available && !undo) {
    return (
      <>
        <div className="row">{label}</div>
        {children}
      </>
    );
  }

  const canSubmit = !enhance.isPending && !!(value.trim() || note.trim());
  const submit = () => {
    if (!slug || !canSubmit) return;
    enhance.mutate(
      { kind, workflow, prompt: value, note, engine, references, bin: binId ?? null },
      {
        onSuccess: (r) => {
          setUndo({ before: value, after: r.prompt });
          onReplace(r.prompt);
          setDraft(EMPTY);
          toast(r.system_source === "workflow" ? `enhanced with ${workflow.replace(/\.json$/, "")}'s instructions` : "enhanced");
        },
        onError: (err) => toast(errorText(err)),
      },
    );
  };

  return (
    <>
      <div className="row">
        {label}
        <span className="spacer" />
        {undo && <span className="enhance-done">prompt updated</span>}
        {undo && (
          <button
            type="button"
            className="chip enhance-undo"
            title="put the previous prompt back"
            onClick={() => {
              onReplace(undo.before);
              setUndo(null);
            }}
          >
            ↶ undo
          </button>
        )}
        {available && (
          <button type="button" className={`agent-pill enhance-pill ${open ? "is-on" : ""}`} onClick={() => setOpen(!open)} title="rewrite this prompt with AI">
            ✦ Enhance
          </button>
        )}
      </div>
      {children}
      {open && available && (
        <div className="enhance-sheet">
          <div className="tag">NOTE FOR THE REWRITE</div>
          <AutoTextarea
            className="field-input veil-text"
            value={note}
            onChange={setNote}
            placeholder="optional — e.g. focus more on the face, slower camera, make it a night scene…"
            disabled={enhance.isPending}
            autoFocus
            onSubmitKey={submit}
          />
          <div className="row chips">
            {(["agent", "local"] as const).map((e) => {
              const can = e === "agent" ? agent : local;
              return (
                <button
                  key={e}
                  type="button"
                  className={`chip ${engine === e ? "is-on" : ""}`}
                  onClick={() => setEngine(e)}
                  disabled={enhance.isPending || !can}
                  title={can ? undefined : e === "agent" ? "start an agent session to use the agent" : "no local enhancer workflow, or ComfyUI is down"}
                >
                  {e === "local" ? "local model" : "agent"}
                </button>
              );
            })}
          </div>
          <div className="row enhance-actions">
            {live && <JobStatusLine job={live} />}
            <button type="button" className="btn-outline" onClick={() => setOpen(false)} disabled={enhance.isPending}>
              Close
            </button>
            <button type="button" className="btn-primary" onClick={submit} disabled={!canSubmit} title={agentTitle ?? "Enter in the note"}>
              {enhance.isPending ? (
                <>
                  <span className="spinner" aria-hidden="true" />
                  {(live && jobStepLabel(live)) ?? agentLabel ?? (engine === "agent" ? "asking the agent…" : "enhancing…")}
                </>
              ) : (
                "Enhance"
              )}
            </button>
          </div>
          <div className="hint">replaces the prompt above — nothing is generated until you press Generate</div>
        </div>
      )}
    </>
  );
}
