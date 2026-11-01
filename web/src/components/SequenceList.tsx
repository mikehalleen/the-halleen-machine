import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { liveJobsFor, useLiveJobs, useProject } from "../api/client";
import type { SequenceItemOut } from "../api/types";
import { useAgentUi } from "../layout/AgentUi";
import { MediaThumb } from "./MediaThumb";
import { SortableItem, SortableList } from "./Sortable";

const REMOVE_ARM_MS = 3000;
/** The drag payload a sidebar row carries: a bin id, dropped into the sequence. */
export const BIN_DRAG_TYPE = "application/x-thm-bin";
const STILL_STEP = 0.5;
const STILL_MIN = 0.5;

interface Props {
  slug: string;
  seqId: string;
  items: SequenceItemOut[];
  wide: boolean;
  /** The open row (its bin), owned by the screen: a tap opens, playback moves it along. */
  open: string | null;
  onOpen: (bin: string | null) => void;
  onReorder: (order: string[]) => Promise<unknown>;
  onRemove: (bin: string) => void;
  onStill: (bin: string, seconds: number) => void;
  /** A bin dropped in from the rail: add it before `at` (an index), or at the end. */
  onDropBin?: (bin: string, at: number | null) => void;
}

/** A human line for one agent-only setting: `gain −6 dB`, `sound · room tone`, `dissolve out 0.5s`. */
export function agentChip(key: string, value: unknown): string {
  const k = key.replace(/_s$/, "").replace(/_db$/, "").replace(/_/g, " ");
  if (value === true) return k;
  if (key.endsWith("_db")) return `${k} ${Number(value) > 0 ? "+" : ""}${value} dB`;
  if (key.endsWith("_s")) return `${k} ${value}s`;
  if (typeof value === "object" && value !== null) return `${k} · ${JSON.stringify(value)}`;
  return `${k} · ${String(value)}`;
}

function line2(item: SequenceItemOut): string {
  if (item.kind === "title") return "no take · plays as title card";
  if (item.kind === "still") return "still";
  const m = item.media;
  const take = m?.take ? `take ${m.take}` : "clip";
  const size = m?.width && m?.height ? `${m.width}×${m.height}` : "";
  const len = m?.duration_s != null ? `${Number(m.duration_s).toFixed(1)}s` : "";
  return [take, size, len].filter(Boolean).join(" · ");
}

/**
 * The item rows: collapsed by default, one open at a time. A tap opens a row and parks the
 * player on it; playback opens the row it is on and scrolls it into view. On a desk the whole
 * row drags (a click is still a click: the drag starts after 6px); on a phone only the grip
 * grip with the thumbnail does, wide enough to hold, so a swipe elsewhere scrolls. Agent-only settings show
 * as read-only chips inside the open row; a trim shows as the kept range, a dropped trim as a
 * warning. The agent's settings on an item are the gray chips inside its open row; the feed is
 * where its edits are announced. Remove is two taps.
 */
export function SequenceList({ slug, seqId, items, wide, open, onOpen, onReorder, onRemove, onStill, onDropBin }: Props) {
  const [armed, setArmed] = useState<string | null>(null);
  const live = useLiveJobs(slug);
  const rendering = (bin: string) => liveJobsFor(bin, live.data, undefined).length;
  // the take an item plays is one nobody has looked at yet: what the export would now carry
  // changed. A new take that is not the item's current one changes nothing here, so no mark
  const project = useProject(slug);
  const freshCurrent = (item: SequenceItemOut) =>
    !!item.media && (project.data?.bins.find((b) => b.id === item.bin)?.unseen ?? []).includes(item.media.id);
  // a bin from the rail hovering over the list: which row it would land before (null = the end)
  const [dropAt, setDropAt] = useState<number | null | undefined>(undefined);
  const hasBin = (e: React.DragEvent) => Array.from(e.dataTransfer.types).includes(BIN_DRAG_TYPE);
  const rowIndexAt = (e: React.DragEvent): number | null => {
    const row = (e.target as HTMLElement).closest<HTMLElement>("[data-bin]");
    if (!row) return null;
    const rect = row.getBoundingClientRect();
    const idx = Number(row.dataset.index);
    return e.clientY < rect.top + rect.height / 2 ? idx : idx + 1;
  };
  const onDragOver = (e: React.DragEvent) => {
    if (!onDropBin || !hasBin(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
    const at = rowIndexAt(e);
    setDropAt(at === null || at >= items.length ? null : at);
  };
  const onDrop = (e: React.DragEvent) => {
    if (!onDropBin || !hasBin(e)) return;
    e.preventDefault();
    const bin = e.dataTransfer.getData(BIN_DRAG_TYPE);
    const at = dropAt === undefined ? null : dropAt;
    setDropAt(undefined);
    if (bin && !items.some((i) => i.bin === bin)) onDropBin(bin, at);
  };
  const armTimer = useRef<number | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  // the agent panel's note is about the open row; nothing once this list is gone
  const { setOpenItem } = useAgentUi();
  useEffect(() => {
    setOpenItem(open && items.some((i) => i.bin === open) ? open : null);
  }, [open, items, setOpenItem]);
  useEffect(() => () => setOpenItem(null), [setOpenItem]);
  // the open row stays in view as playback walks the list
  useEffect(() => {
    if (!open) return;
    listRef.current?.querySelector(`[data-bin="${open}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [open]);

  const disarm = () => {
    if (armTimer.current !== null) window.clearTimeout(armTimer.current);
    armTimer.current = null;
    setArmed(null);
  };
  useEffect(() => disarm, []);

  const toggle = (bin: string) => {
    disarm();
    onOpen(open === bin ? null : bin);
  };

  const remove = (bin: string) => {
    if (armed === bin) {
      disarm();
      onOpen(null);
      onRemove(bin);
      return;
    }
    disarm();
    setArmed(bin);
    armTimer.current = window.setTimeout(() => setArmed(null), REMOVE_ARM_MS);
  };

  const byBin = new Map(items.map((i) => [i.bin, i]));
  const ids = items.map((i) => i.bin);

  return (
    <SortableList ids={ids} onCommit={onReorder} layout="vertical">
      {(order) => (
        <div
          className={`seq-list ${dropAt !== undefined ? "is-drop" : ""}`}
          ref={listRef}
          onDragOver={onDragOver}
          onDragLeave={(e) => {
            if (!listRef.current?.contains(e.relatedTarget as Node)) setDropAt(undefined);
          }}
          onDrop={onDrop}
        >
          {order.map((bin, idx) => {
            const item = byBin.get(bin);
            if (!item) return null;
            const isOpen = open === bin;
            const trimmed = item.kind === "video" && (item.head_s > 0 || item.tail_s > 0);
            const total = item.media?.duration_s ?? 0;
            const keptLeft = total > 0 ? (item.head_s / total) * 100 : 0;
            const keptWidth = total > 0 ? Math.max(0, ((total - item.head_s - item.tail_s) / total) * 100) : 100;
            // the summary is the player's line; the chips are the settings behind it
            const agentKeys = Object.entries(item.agent ?? {}).filter(([k]) => k !== "summary");
            const agentSummary = typeof item.agent?.summary === "string" ? item.agent.summary : "";
            return (
              <SortableItem key={bin} id={bin}>
                {(s) => (
                  <div
                    {...s.item}
                    {...(wide ? s.pointerHandle : {})}
                    className={`seq-row ${isOpen ? "is-open" : ""} ${s.isDragging ? "is-dragging" : ""} ${item.kind === "title" ? "is-title" : ""} ${wide ? "is-grabbable" : ""} ${dropAt === idx ? "is-drop-before" : ""}`}
                    onClick={() => toggle(bin)}
                    data-bin={bin}
                    data-index={idx}
                  >
                    <div className="seq-row-main">
                      {/* the hold target: grip, number and thumbnail together, so a finger has room to land */}
                      <span {...s.handle} className="seq-hold" aria-label={`Drag ${item.name}`} title="hold to drag">
                        <span className="seq-grip" aria-hidden="true">
                          <i />
                          <i />
                          <i />
                        </span>
                        {wide && <span className="seq-pos">{idx + 1}</span>}
                        {item.kind === "title" ? (
                          <span className="seq-thumb-empty" aria-label="no take">
                            NO TAKE
                          </span>
                        ) : (
                          <MediaThumb slug={slug} id={item.media?.id} type={item.media?.type} size={44} passive />
                        )}
                      </span>
                      <span className="seq-row-text">
                        <span className="seq-row-name">
                          {item.shot != null && <span className="shot-no">{item.shot}</span>}
                          {item.name}
                        </span>
                        <span className="seq-row-meta">
                          {line2(item)}
                          {trimmed && !item.trim_dropped && (
                            <>
                              {" "}
                              <span className="seq-trim-bar" aria-hidden="true">
                                <span className="seq-trim-kept" style={{ left: `${keptLeft}%`, width: `${keptWidth}%` }} />
                              </span>
                            </>
                          )}
                        </span>
                        {item.trim_dropped && (
                          <span className="seq-row-chips">
                            <span className="seq-chip seq-chip-warn">! trim values removed due to length change</span>
                          </span>
                        )}
                      </span>
                      {isOpen && item.kind === "still" ? (
                        <span className="seq-stepper" onClick={(e) => e.stopPropagation()}>
                          <button type="button" className="seq-step" aria-label="Shorter" disabled={(item.still_s ?? 3) - STILL_STEP < STILL_MIN} onClick={() => onStill(bin, Math.max(STILL_MIN, (item.still_s ?? 3) - STILL_STEP))}>
                            −
                          </button>
                          <span className="seq-step-value">{item.duration_s.toFixed(1)}s</span>
                          <button type="button" className="seq-step" aria-label="Longer" onClick={() => onStill(bin, (item.still_s ?? 3) + STILL_STEP)}>
                            +
                          </button>
                        </span>
                      ) : (
                        <span className="seq-dur-wrap">
                        {rendering(bin) > 0 && <span className="row-live" role="status" aria-label="generating" title={`${rendering(bin)} generating`} />}
                        {freshCurrent(item) && (
                          <span className="row-new" title="a new take is this item's current one now">
                            NEW
                          </span>
                        )}
                        <span className={`seq-dur ${item.kind === "title" ? "is-muted" : ""} ${item.trim_dropped ? "is-warn" : ""}`}>
                          {item.kind === "title" ? `(${item.duration_s.toFixed(1)}s)` : `${item.duration_s.toFixed(1)}s`}
                        </span>
                        </span>
                      )}
                    </div>
                    {isOpen && (
                      <div className="seq-row-open" onClick={(e) => e.stopPropagation()}>
                        {(agentKeys.length > 0 || agentSummary) && (
                          <div className="seq-agent-line">
                            <span className="tag">EXPORT ONLY</span>
                            {agentSummary && <span className="seq-agent-summary">{agentSummary}</span>}
                            {agentKeys.map(([k, v]) => (
                              <span key={k} className="seq-chip seq-chip-ro" title={`${k}: ${JSON.stringify(v)}`}>
                                {agentChip(k, v)}
                              </span>
                            ))}
                          </div>
                        )}
                        <div className="row seq-row-actions">
                          <Link to={`/p/${encodeURIComponent(slug)}/b/${encodeURIComponent(bin)}`} className="btn-outline seq-pill" title="the bin: pick a take, change the recipe">
                            ↗ Open bin
                          </Link>
                          {item.kind === "video" && item.media && (
                            <Link to={`/p/${encodeURIComponent(slug)}/s/${encodeURIComponent(seqId)}/clip/${encodeURIComponent(bin)}`} className="btn-outline seq-pill" title="trim this item's head and tail; the take stays whole">
                              ✂ Clip
                            </Link>
                          )}
                          <span className="spacer" />
                          <button type="button" className={`btn-outline seq-pill ${armed === bin ? "is-armed" : ""}`} onClick={() => remove(bin)} title="take this bin out of the sequence; the bin stays">
                            {armed === bin ? (wide ? "Click again to remove" : "Tap again to remove") : "✕ Remove"}
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </SortableItem>
            );
          })}
          {/* an empty list has no height to drop on: a zone stands in for it until the first item */}
          {items.length === 0 && onDropBin && dropAt === undefined && <div className="seq-drop-empty">drag a bin here from the list on the left</div>}
          {dropAt === null && onDropBin && <div className="seq-drop-end">{items.length === 0 ? "drop to add it" : "drop to add at the end"}</div>}
        </div>
      )}
    </SortableList>
  );
}
