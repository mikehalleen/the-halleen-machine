import { Link } from "react-router-dom";
import { liveJobsFor, useLiveJobs } from "../api/client";
import type { SequenceCard } from "../api/types";
import { MediaThumb } from "./MediaThumb";
import { seqClock } from "./SequencePlayer";

interface Props {
  slug: string;
  sequences: SequenceCard[];
  active?: string;
  onDelete: (seq: SequenceCard) => void;
  /** The sidebar's narrower rows. */
  compact?: boolean;
}

/** The sequences tab: a card per sequence with its count and length, an X styled like the bin X.
 * The bin rows' marks, for the cut: a pulse while one of its items' bins renders, and how many
 * items now play a take nobody has looked at. */
export function SequenceCards({ slug, sequences, active, onDelete, compact }: Props) {
  const live = useLiveJobs(slug);
  const rendering = (s: SequenceCard) => (s.bins ?? []).reduce((n, b) => n + liveJobsFor(b, live.data, undefined).length, 0);
  return (
    <div className={`seq-cards ${compact ? "is-compact" : ""}`}>
      {sequences.length === 0 && <div className="hint">no sequences yet</div>}
      {sequences.map((s) => (
        <Link key={s.id} to={`/p/${encodeURIComponent(slug)}/s/${encodeURIComponent(s.id)}`} className={`${compact ? "sidebar-row" : "card shot-row"} seq-card ${s.id === active ? "is-active" : ""}`}>
          {s.thumb ? (
            <MediaThumb slug={slug} id={s.thumb.id} type={s.thumb.type} size={compact ? 36 : 56} passive />
          ) : (
            <span className="seq-card-glyph" aria-hidden="true">
              ▤
            </span>
          )}
          <span className={compact ? "sidebar-row-text" : "shot-row-text"}>
            <b className={compact ? "sidebar-row-name" : "shot-row-name"}>{s.name}</b>
            <span className={compact ? "sidebar-row-kind" : "meta"}>
              {s.items} item{s.items === 1 ? "" : "s"} · {seqClock(s.total_s)}
            </span>
          </span>
          {rendering(s) > 0 && <span className="row-live" role="status" aria-label="generating" title={`${rendering(s)} generating for this sequence`} />}
          {s.fresh > 0 && (
            <span className="row-new" title={`${s.fresh} item${s.fresh === 1 ? "" : "s"} now play a new take`}>
              {s.fresh}
            </span>
          )}
          <button
            type="button"
            className="row-delete"
            title={`delete ${s.name}`}
            aria-label={`Delete ${s.name}`}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onDelete(s);
            }}
          >
            ✕
          </button>
        </Link>
      ))}
    </div>
  );
}
