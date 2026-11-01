import { memo, useState } from "react";
import { type DecisionAction } from "../api/client";
import type { DecisionStatus, FeedEntry, ShotDecision, ShotOut } from "../api/types";
import { AutoTextarea } from "./AutoTextarea";
import { MediaThumb } from "./MediaThumb";

interface Props {
  slug: string;
  entry: FeedEntry;
  onDecide: (id: string, action: DecisionAction, note: string, items?: ShotDecision[]) => void;
  onEdit: (binId: string) => void;
  busy?: boolean;
}

const LABELS: Record<DecisionStatus, string> = {
  pending: "PENDING",
  proposed: "PROPOSED",
  approved: "APPROVED ✓",
  rejected: "REJECTED ✕",
  superseded: "SUPERSEDED",
  closed: "CLOSED",
};

type Draft = { enabled: boolean; description: string };

function seed(shots: ShotOut[]): Draft[] {
  return shots.map((s) => ({ enabled: !s.dropped, description: s.description }));
}

/**
 * The pre-generation card: one intended render, before it spends. A batch is one of these per
 * shot, each approved, rewritten or closed on its own (the batch's title and reason are the
 * agent's line ahead of them). The one-liner is the user's to rewrite; the one button approves
 * it (or sends a note with it), and approval makes the bin, writes the recipe, binds the
 * references and queues the render in one round trip. After the decision the same card reads
 * as the record: what was kept or rewritten, the bin it made, and the render folds in under it
 * as its own card. A card with several items (an older record) keeps the per-item toggles.
 *
 * The drafts are local state seeded once from the entry: a feed refetch that returns the same
 * card never touches what the user is typing. Memoized like ChangeCard, for the same reason.
 */
export const ShotsCard = memo(ShotsCardBody);

function ShotsCardBody({ slug, entry, onDecide, onEdit, busy }: Props) {
  const change = entry.change!;
  const shots = entry.shots ?? [];
  const owed = !!change.decision;
  const status: DecisionStatus = change.decision?.status ?? "pending";
  const open = owed && status === "proposed";
  const canReopen = status === "rejected" || status === "superseded" || status === "closed";
  const [drafts, setDrafts] = useState<Draft[]>(() => seed(shots));
  const [note, setNote] = useState("");
  const sentNote = !open ? change.decision?.note : null;
  const kept = drafts.filter((d) => d.enabled).length;
  const n = shots.length;
  const one = n === 1;

  const setAll = (enabled: boolean) => setDrafts((ds) => ds.map((d) => ({ ...d, enabled })));
  const toggle = (i: number) => setDrafts((ds) => ds.map((d, j) => (j === i ? { ...d, enabled: !d.enabled } : d)));
  const edit = (i: number, description: string) => setDrafts((ds) => ds.map((d, j) => (j === i ? { ...d, description } : d)));
  const submit = () => {
    if (busy) return;
    if (kept === 0) {
      onDecide(change.id, "close", "");
      return;
    }
    const items: ShotDecision[] = drafts.map((d, index) => ({
      index,
      enabled: d.enabled,
      description: d.enabled && d.description.trim() !== shots[index]?.description ? d.description.trim() : null,
    }));
    onDecide(change.id, "approve", note, items);
  };
  const label = kept === 0 ? "close — nothing kept" : note.trim() ? "send →" : one ? "✓ approve" : kept === n ? `✓ approve all ${n}` : `✓ approve ${kept} of ${n}`;

  return (
    <div className={`change-card shots-card is-${status}`}>
      <div className="row change-head">
        <span className="tag">{one ? "SHOT" : "SHOTS"}</span>
        {!one && <span className="shots-count">{n} proposed</span>}
        <span className="spacer" />
        <span className="change-status">{owed ? LABELS[status] : "DONE"}</span>
        {open && (
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
      {!one && <div className="change-desc">{change.description.replace(/^proposed:\s*/i, "")}</div>}
      {change.reason && <div className="change-reason">{change.reason}</div>}
      <ol className="shot-list">
        {shots.map((s, i) => {
          const d = drafts[i] ?? { enabled: !s.dropped, description: s.description };
          const enabled = open ? d.enabled : !s.dropped;
          return (
            <li key={s.index} className={`shot-row ${enabled ? "" : "is-dropped"}`}>
              {!one && (
                <button
                  type="button"
                  className={`chip shot-toggle ${enabled ? "is-on" : ""}`}
                  aria-pressed={enabled}
                  disabled={!open || busy}
                  onClick={() => toggle(i)}
                  title={open ? (enabled ? "drop this one" : "keep this one") : enabled ? "kept" : "dropped"}
                >
                  {i + 1}
                </button>
              )}
              <div className="shot-body">
                <div className="row wrap shot-head">
                  <b className="shot-name">{s.name}</b>
                  <span className="shot-meta">
                    {s.kind ?? "render"}
                    {s.length_s ? ` · ${s.length_s}s` : ""}
                    {s.kind === "video" ? ` · ${s.quarter ? "quarter" : "full"}` : ""}
                  </span>
                  {s.references.length > 0 && (
                    <span className="shot-refs">
                      {s.references.map((r) => (
                        <span key={r.media} className="shot-ref" title={`.${r.label}`}>
                          <MediaThumb slug={slug} id={r.media} type={r.type} size={22} passive />
                          <span className="shot-ref-label">.{r.label}</span>
                        </span>
                      ))}
                    </span>
                  )}
                </div>
                {open ? (
                  <AutoTextarea className="shot-desc" minRows={1} value={d.description} onChange={(v) => edit(i, v)} aria-label={`Shot ${i + 1}`} disabled={!enabled || busy} />
                ) : (
                  <div className="shot-desc-text">
                    {s.description}
                    {s.edited && <span className="shot-flag"> · rewritten</span>}
                  </div>
                )}
                {!open && (
                  <div className="row wrap shot-outcome">
                    {s.dropped ? (
                      <span className="shot-flag">dropped</span>
                    ) : s.error ? (
                      <span className="shot-flag is-error">render refused: {s.error}</span>
                    ) : (
                      <>
                        {s.bin && (
                          <button type="button" className="link" onClick={() => onEdit(s.bin!.id)}>
                            open {s.bin.name} →
                          </button>
                        )}
                        {s.bin && s.edited && s.has_prompt && <span className="shot-flag">no render — the agent rewrites the prompt first</span>}
                      </>
                    )}
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      {open && !one && (
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
        <AutoTextarea
          className="change-note"
          placeholder="reply, redirect, ask — or just approve…"
          value={note}
          onChange={setNote}
          aria-label="Note"
          onSubmitKey={submit}
        />
      )}
      {sentNote && <div className="change-sent">“{sentNote}” — sent to agent</div>}
      {open && (
        <div className="row change-actions">
          <button type="button" className="btn-approve" disabled={busy} onClick={submit} title="Enter to send">
            {label}
          </button>
        </div>
      )}
    </div>
  );
}
