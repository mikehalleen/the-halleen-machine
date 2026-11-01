import { memo } from "react";
import { type DecisionAction } from "../api/client";
import type { DecisionStatus, FeedEntry, ShotDecision, ShotOut } from "../api/types";
import { useMediaViewer } from "../layout/useMediaViewer";
import { AutoTextarea } from "./AutoTextarea";
import { CardReason, shotsQuestion } from "./cardQuestion";
import { MediaThumb } from "./MediaThumb";
import { NoteFilesArea, SentFiles, useNoteFiles } from "./NoteFiles";
import { useDraft } from "./useDraft";

interface Props {
  slug: string;
  entry: FeedEntry;
  onDecide: (id: string, action: DecisionAction, note: string, items?: ShotDecision[], hold?: boolean, files?: string[]) => void;
  onEdit: (binId: string) => void;
  busy?: boolean;
}

const LABELS: Record<DecisionStatus, string> = {
  pending: "PENDING",
  proposed: "PROPOSED",
  approved: "QUEUED ✓", // a shot card's yes is a render started, not a verdict on anything
  rejected: "REJECTED ✕",
  superseded: "SUPERSEDED",
  closed: "CLOSED",
  replied: "REPLIED",
};

type Draft = { enabled: boolean; description: string };
type Drafts = { note: string; items: Draft[] };

function seed(shots: ShotOut[]): Draft[] {
  return shots.map((s) => ({ enabled: !s.dropped, description: s.description }));
}

/**
 * The pre-generation card: one intended render, before it spends. A batch is one of these per
 * shot, each approved, rewritten, replied to or closed on its own (the batch's title and reason
 * are the agent's line ahead of them). The card leads with the question (`cardQuestion`: "Do you
 * want to regenerate this video at full quality?") and the buttons answer it. The word is Generate, never "approve": the click greenlights a
 * render, it does not agree with the description, and Approve is kept for a finished take. Generate --
 * the bin is made, the recipe written, the references bound and the render queued in one round
 * trip; Later (a full-quality shot only) does the same but saves the render held, to release by
 * hand; No closes the card and nothing runs. The one-liner is the user's to rewrite first. Typing a note turns the button into "send": a reply, never a go
 * -- nothing runs, the agent reads the note and answers with a revised card; the bare `approve`
 * link stays on a REPLIED card. After the decision the same card reads as the record: what was
 * kept or rewritten, the bin it made, and the render folds in under it as its own card. A card
 * with several items (an older record) keeps the per-item toggles.
 *
 * The drafts (toggles, rewrites, the note) survive navigation and reloads (`useDraft`), keyed
 * on the change, and clear when the card is decided. Memoized like ChangeCard, for the same reason.
 */
export const ShotsCard = memo(ShotsCardBody);

function ShotsCardBody({ slug, entry, onDecide, onEdit, busy }: Props) {
  const change = entry.change!;
  const shots = entry.shots ?? [];
  const owed = !!change.decision;
  const status: DecisionStatus = change.decision?.status ?? "pending";
  const open = owed && status === "proposed";
  const replied = owed && status === "replied";
  const canReopen = status === "rejected" || status === "superseded" || status === "closed";
  const initial: Drafts = { note: "", items: seed(shots) };
  const [draft, setDraft] = useDraft<Drafts>(`${slug}.${change.id}`, initial);
  const drafts = draft.items.length === shots.length ? draft.items : initial.items;
  const note = draft.note;
  const setNote = (v: string) => setDraft((d) => ({ ...d, note: v }));
  const setDrafts = (f: (ds: Draft[]) => Draft[]) => setDraft((d) => ({ ...d, items: f(d.items.length === shots.length ? d.items : seed(shots)) }));
  const sentNote = !open ? change.decision?.note : null;
  const sentFiles = !open ? change.decision?.files : null;
  const attach = useNoteFiles(slug, `${slug}.${change.id}`, open);
  // words or files: either makes the button a send, and the verdict a reply
  const saying = !!note.trim() || attach.any;
  const kept = drafts.filter((d) => d.enabled).length;
  const n = shots.length;
  const one = n === 1;
  const viewer = useMediaViewer();

  const setAll = (enabled: boolean) => setDrafts((ds) => ds.map((d) => ({ ...d, enabled })));
  const toggle = (i: number) => setDrafts((ds) => ds.map((d, j) => (j === i ? { ...d, enabled: !d.enabled } : d)));
  const edit = (i: number, description: string) => setDrafts((ds) => ds.map((d, j) => (j === i ? { ...d, description } : d)));
  const items = (): ShotDecision[] =>
    drafts.map((d, index) => ({
      index,
      enabled: d.enabled,
      description: d.enabled && d.description.trim() !== shots[index]?.description ? d.description.trim() : null,
    }));
  /** Yes, or (`hold`) Later: the same approval, with the renders saved held instead of queued. */
  const approve = (hold = false) => {
    if (busy) return;
    if (kept === 0) {
      setDraft(initial);
      onDecide(change.id, "close", "");
      return;
    }
    const verdict = items();
    setDraft((d) => ({ ...d, note: "" })); // the rewrites ride along; the note is spent
    onDecide(change.id, "approve", "", verdict, hold);
  };
  const submit = () => {
    if (busy) return;
    if (saying) {
      if (attach.uploading > 0) return;
      // a reply: the rewrites stay in the card for the bare approve later
      const text = note;
      const files = attach.ids;
      setDraft((d) => ({ ...d, note: "" }));
      onDecide(change.id, "reply", text, undefined, undefined, files);
      return;
    }
    approve();
  };
  const label = saying ? "send →" : kept === 0 ? "close — nothing kept" : one ? "Generate" : kept === n ? `Generate all ${n}` : `Generate ${kept} of ${n}`;
  // the step up in cost can wait: Later sets everything up and saves the render to release by hand
  const canHold = !saying && kept > 0 && shots.some((s, i) => s.full_quality && (drafts[i]?.enabled ?? true));

  return (
    <div id={`change-${change.id}`} className={`change-card shots-card is-${status}`}>
      <div className="row change-head">
        <span className="tag">{one ? "SHOT" : "SHOTS"}</span>
        {!one && <span className="shots-count">{n} proposed</span>}
        <span className="spacer" />
        <span className="change-status">{owed ? (status === "approved" && shots.some((s) => s.held) ? "SAVED FOR LATER" : LABELS[status]) : "DONE"}</span>
        {replied && (
          <button type="button" className="link" onClick={() => approve()} disabled={busy} title="generate it as it stands, no words">
            generate
          </button>
        )}
        {replied && (
          <button type="button" className="link change-close" onClick={() => onDecide(change.id, "close", "")} disabled={busy} title="seen — nothing owed, nothing sent">
            close
          </button>
        )}
        {canReopen && (
          <button type="button" className="link" onClick={() => onDecide(change.id, "reopen", note)} disabled={busy}>
            reopen
          </button>
        )}
      </div>
      {open && <div className="change-question">{shotsQuestion(shots)}</div>}
      {!one && <div className={`change-desc ${open ? "is-detail" : ""}`}>{change.description.replace(/^proposed:\s*/i, "")}</div>}
      {change.reason && <CardReason text={change.reason} />}
      <ol className="shot-list">
        {shots.map((s, i) => {
          const d = drafts[i] ?? { enabled: !s.dropped, description: s.description };
          const editable = open || replied;
          const enabled = editable ? d.enabled : !s.dropped;
          return (
            <li key={s.index} className={`shot-row ${enabled ? "" : "is-dropped"}`}>
              {!one && (
                <button
                  type="button"
                  className={`chip shot-toggle ${enabled ? "is-on" : ""}`}
                  aria-pressed={enabled}
                  disabled={!editable || busy}
                  onClick={() => toggle(i)}
                  title={editable ? (enabled ? "drop this one" : "keep this one") : enabled ? "kept" : "dropped"}
                >
                  {i + 1}
                </button>
              )}
              <div className="shot-body">
                <div className="row wrap shot-head">
                  {s.shot != null && <span className="shot-no">{s.shot}</span>}
                  <b className="shot-name">“{s.name}”</b>
                  <span className="shot-meta">
                    {s.kind ?? "generation"}
                    {s.length_s ? ` · ${s.length_s}s` : ""}
                    {s.kind === "video" ? ` · ${s.quarter ? "quarter size" : "full size"}` : ""}
                    {s.full_quality ? " · full quality" : ""}
                  </span>
                </div>
                {/* a retake often starts from the bin's current take: say these are what goes in,
                    or the picture reads as the thing being approved */}
                {s.references.length > 0 && (
                  <span className="shot-desc-label">
                    {s.references_from_bin
                      ? "references it generates from (the bin's, as bound now)"
                      : s.kind === "video"
                      ? "references the clip generates from — these pictures stay as they are"
                      : s.takes > 0
                        ? "references for the new take — it is added to the bin"
                        : "references it generates from"}
                  </span>
                )}
                {/* a reference this shot waits on: named on the card so a card left open reads as
                    waiting, not forgotten, and flips to ready when that bin has a take */}
                {(s.needs ?? []).length > 0 && (
                  <div className="shot-needs">
                    {(s.needs ?? []).map((n, k) => (
                      <span key={k} className={`shot-need ${n.ready ? "is-ready" : ""}`} title={n.ready ? "that bin has a take now — ask the agent to bind it and revise this card" : "nothing to bind yet: generate that first"}>
                        {n.ready ? "✓ ready:" : "⏳ waits on"} {n.what}
                        {n.bin_name ? ` — from ${n.bin_name}` : ""}
                      </span>
                    ))}
                  </div>
                )}
                {s.references.length > 0 && (
                  <div className="row wrap shot-refs">
                    {s.references.map((r) => (
                      <span key={r.media} className="shot-ref" title={`@${r.label} — open full size`}>
                        <MediaThumb slug={slug} id={r.media} type={r.type} size={56} onClick={() => viewer.open(slug, r.media)} />
                        <span className="shot-ref-label">@{r.label}</span>
                      </span>
                    ))}
                  </div>
                )}
                {editable && <span className="shot-desc-label">what the agent will generate — edit it if it is off</span>}
                {editable ? (
                  <AutoTextarea className="shot-desc" minRows={1} value={d.description} onChange={(v) => edit(i, v)} aria-label={`Shot ${i + 1}`} disabled={!enabled || busy} />
                ) : (
                  <div className="shot-desc-text">
                    {s.description}
                    {s.edited && <span className="shot-flag"> · rewritten</span>}
                  </div>
                )}
                {!editable && (
                  <div className="row wrap shot-outcome">
                    {s.dropped ? (
                      <span className="shot-flag">dropped</span>
                    ) : s.error ? (
                      <span className="shot-flag is-error">generation refused: {s.error}</span>
                    ) : (
                      <>
                        {s.bin && (
                          <button type="button" className="link" onClick={() => onEdit(s.bin!.id)}>
                            open {s.bin.name} →
                          </button>
                        )}
                        {s.bin && s.edited && s.has_prompt && <span className="shot-flag">no generation — the agent rewrites the prompt first</span>}
                        {s.bin && s.held && <span className="shot-flag">saved for later — start it from the bar when you are ready</span>}
                      </>
                    )}
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      {(open || replied) && !one && (
        <div className="row shot-tools">
          <button type="button" className="link" onClick={() => setAll(true)} disabled={busy}>
            all
          </button>
          <button type="button" className="link" onClick={() => setAll(false)} disabled={busy}>
            none
          </button>
          <span className="spacer" />
          <span className="shot-kept">
            {kept} of {n} kept
          </span>
        </div>
      )}
      {open && (
        <NoteFilesArea slug={slug} note={attach}>
          <AutoTextarea
            className="change-note"
            placeholder="or say what to change instead…"
            value={note}
            onChange={setNote}
            aria-label="Note"
            onSubmitKey={submit}
          />
        </NoteFilesArea>
      )}
      {sentNote &&
        // a close carries no note from you: one on a closed card is why it closed (the agent
        // failed the take, the generation was cancelled), not something you sent
        (status === "closed" ? <div className="change-sent">closed — {sentNote}</div> : <div className="change-sent">“{sentNote}” — sent to agent</div>)}
      {!sentNote && sentFiles?.length ? <div className="change-sent">sent to agent</div> : null}
      <SentFiles slug={slug} files={sentFiles} />
      {open && (
        <div className="row change-actions">
          <button type="button" className="btn-approve is-yes" disabled={busy || attach.uploading > 0} onClick={submit} title={saying ? "Enter to send — a note is a reply, not an approval" : "generate: the generation starts now"}>
            {label}
          </button>
          {canHold && (
            <button type="button" className="btn-approve" disabled={busy} onClick={() => approve(true)} title="generate, but not now: everything is set up and the generation is saved — start it from the bar when you are ready">
              Later
            </button>
          )}
          {!saying && kept > 0 && (
            <button type="button" className="btn-approve" disabled={busy} onClick={() => onDecide(change.id, "close", "")} title="no: nothing runs, the card closes">
              No
            </button>
          )}
        </div>
      )}
    </div>
  );
}
