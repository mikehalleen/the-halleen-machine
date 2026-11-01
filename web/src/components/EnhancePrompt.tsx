import { useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, errorText, newRequestId, useCancelEnhance, useEnhance, useEnhanceProgress, useEnhanceStatus, type EnhanceProgress } from "../api/client";
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
  /** Why the last rewrite failed; shown in the sheet until the next Enhance press. */
  error: string | null;
  /** What the enhancer wrote that was thrown away, one per attempt, shown under the error. */
  replies: string[];
}

const EMPTY: Draft = { open: false, note: "", engine: null, error: null, replies: [] };

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
    if (draft.open || draft.note || draft.error) sessionStorage.setItem(`thm.enhance.${key}`, JSON.stringify(draft));
    else sessionStorage.removeItem(`thm.enhance.${key}`);
  } catch {
    /* storage unavailable: the sheet still works for this visit */
  }
}

/**
 * The `✦ Enhance` pill beside a prompt label and the sheet it opens: a note, an engine choice
 * when there is one, and Enhance / Close. The rewrite replaces the prompt and the sheet closes;
 * nothing is generated. One undo is kept in memory until the prompt is edited again. While the
 * local model works, Close becomes Cancel: the run is stopped and ComfyUI drops the prompt.
 *
 * Hidden entirely when no engine can serve — no local enhancer workflow, ComfyUI down, no agent.
 */
export function EnhancePrompt({ label, children, slug, kind, workflow, value, references, onReplace, agentLive, storageKey, binId }: Props) {
  const status = useEnhanceStatus(slug);
  const enhance = useEnhance(slug);
  const cancel = useCancelEnhance(slug);
  // the name this run was sent under, so Cancel can stop it; null between runs
  const requestId = useRef<string | null>(null);
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
  const { open, note, engine: picked, error, replies = [] } = draft;
  const setError = (error: string | null) => setDraft((d) => ({ ...d, error, replies: [] }));
  const setOpen = (open: boolean) => setDraft((d) => ({ ...d, open }));
  const setNote = (note: string) => setDraft((d) => ({ ...d, note }));
  const setEngine = (engine: EnhanceEngine) => setDraft((d) => ({ ...d, engine }));
  useEffect(() => writeDraft(storageKey, draft), [storageKey, draft]);
  /** `{ before, after }` of the last rewrite: undo restores `before` while the prompt still reads `after`.
   * `dropped` names the reference tags the rewrite left out; the warning lives as long as the undo. */
  const [undo, setUndo] = useState<{ before: string; after: string; dropped: string[] } | null>(null);

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
    const request = newRequestId();
    requestId.current = request;
    setError(null);
    enhance.mutate(
      { kind, workflow, prompt: value, note, engine, references, bin: binId ?? null, request },
      {
        onSuccess: (r) => {
          setUndo({ before: value, after: r.prompt, dropped: r.dropped_tags ?? [] });
          onReplace(r.prompt);
          setDraft(EMPTY);
          toast(r.system_source === "workflow" ? `enhanced with ${workflow.replace(/\.json$/, "")}'s instructions` : "enhanced");
        },
        // a cancel is the user's own doing: the prompt stays as it was, no error to report. A real
        // failure stays in the sheet (opened if it was closed) until the next Enhance press
        onError: (err) => {
          if (err instanceof ApiError && err.code === "enhance_cancelled") toast("enhance cancelled");
          else {
            const raw = err instanceof ApiError ? err.extra.replies : undefined;
            const replies = Array.isArray(raw) ? raw.filter((r): r is string => typeof r === "string" && !!r.trim()) : [];
            setDraft((d) => ({ ...d, open: true, error: errorText(err), replies }));
          }
        },
        onSettled: () => {
          requestId.current = null;
        },
      },
    );
  };
  // only the local model can be stopped: the agent engine has no run to interrupt
  const cancellable = enhance.isPending && engine === "local" && !!requestId.current;
  const doCancel = () => {
    if (!requestId.current || cancel.isPending) return;
    cancel.mutate(requestId.current, { onError: (err) => setError(errorText(err)) });
  };

  return (
    <>
      <div className="row">
        {label}
        <span className="spacer" />
        {undo && (undo.dropped.length > 0 ? (
          <span className="enhance-dropped" title="the rewrite no longer mentions these references, so the generation will not use them where the prompt placed them; undo, or add the tags back">
            prompt updated · dropped {undo.dropped.join(", ")}
          </span>
        ) : (
          <span className="enhance-done">prompt updated</span>
        ))}
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
          {error && !enhance.isPending && (
            <div className="enhance-error" role="alert">
              <b>enhance failed</b> {error}
              {replies.map((r, i) => (
                <div key={i} className="enhance-reply">
                  <div className="enhance-reply-label">{replies.length > 1 ? `what it wrote (try ${i + 1})` : "what it wrote"}</div>
                  <div className="enhance-reply-text veil-text veil-read" tabIndex={0}>{r}</div>
                </div>
              ))}
            </div>
          )}
          <div className="row enhance-actions">
            {live && <JobStatusLine job={live} />}
            {cancellable ? (
              <button type="button" className="btn-outline" onClick={doCancel} disabled={cancel.isPending} title="stop the rewrite — the prompt stays as it is">
                {cancel.isPending ? "cancelling…" : "Cancel"}
              </button>
            ) : (
              <button type="button" className="btn-outline" onClick={() => setOpen(false)} disabled={enhance.isPending}>
                Close
              </button>
            )}
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
