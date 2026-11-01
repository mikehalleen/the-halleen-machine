import { useCallback, useEffect, useRef, useState, type ChangeEvent, type CSSProperties } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { errorText, liveJobsFor, uploadSummary, useBin, useCancelJob, useCommand, useDiscardHeld, useFeed, useFlipMedia, isGif, useGenerate, useHeld, useLiveJobs, useClearBinSeen, useMarkSeen, useProject, useRecipe, useSetBinTags, useUploadMany, useWorkflows, type LiveBinDetail } from "../api/client";
import { AutoTextarea } from "../components/AutoTextarea";
import { Crumbs, type Crumb } from "../components/Crumbs";
import { libraryCrumb } from "../layout/useLibraryTab";
import { ConfirmSheet } from "../components/ConfirmSheet";
import { EnhancePrompt } from "../components/EnhancePrompt";
import { LengthField } from "../components/LengthField";
import { QuarterToggle, quarterParams, useQuarterSize } from "../components/SizeField";
import { DraftToggle, draftParams, useDraft } from "../components/DraftToggle";
import { LATER_GLYPH, LaterToggle, useLater } from "../components/LaterToggle";
import { Hint } from "../components/Hint";
import { JobStatusLine, execTimeLabel, jobPhaseLine, jobStepLabel } from "../components/JobProgress";
import { LoraRow } from "../components/LoraRow";
import { MediaThumb, ratioOf } from "../components/MediaThumb";
import { RefMention, findMention, insertMention, matchMentions, type Mention, type MentionOption } from "../components/RefMention";
import { TagEditor } from "../components/TagEditor";
import { TakePreview } from "../components/TakePreview";
import { useToast } from "../components/Toast";
import { SortableItem, SortableList } from "../components/Sortable";
import { PostsStrip } from "../components/PostsStrip";
import { StartFromSheet } from "../components/StartFromSheet";
import { UploadSheet } from "../components/UploadSheet";
import { genTime, mediaLabel } from "../components/mediaLabel";
import { requestNotifications } from "../layout/notify";
import { lockVeil } from "../layout/veil";
import { useAgentUi } from "../layout/AgentUi";
import { openCardAbout } from "../layout/Feed";
import { requestJump } from "../layout/jumpCard";
import { Shell } from "../layout/Shell";
import { useSessionPanel } from "../layout/useSessionPanel";
import { VIEW_FOR_PARAM, VIEW_REF_PARAM, useMediaViewer } from "../layout/useMediaViewer";
import { useBreakpoint } from "../layout/useBreakpoint";
import { useCloneBin } from "../layout/useCloneBin";
import { useCreateBin } from "../layout/useCreateBin";
import { useLibraryPicker } from "../layout/useLibraryPicker";
import { pastedInAgentPanel, usePasteFiles } from "../layout/usePasteFiles";
import type { BinDetail, CapacityOut, Generation, JobOut, Lora, MediaType, ReferenceOut } from "../api/types";
import { workflowLabel } from "./ProjectForm";
import { LibraryPicker } from "./LibraryPicker";


/** One line for a toast; runner errors can carry a log tail. */
function firstLine(text: string | null | undefined): string {
  return (text ?? "").split("\n").find((l) => l.trim()) ?? "unknown error";
}

/** How many references of one type the workflow takes (`Capacity.slots_for` on the server). */
export function slotsOf(cap: CapacityOut, type: MediaType): number {
  return type === "video" ? cap.video_slots : cap.image_slots;
}

/**
 * The fixed name of slot `n` for one media type: `image 2`, `video 1`, or on a video workflow
 * that loads frames by position rather than by token, `start frame` / `end frame` (the same
 * rule as `reference_sub_label` in thm_server/derive.py).
 */
export function slotLabel(cap: CapacityOut, type: MediaType, n: number): string {
  const positional = type === "image" && !cap.slot_node_ids.length && (cap.supports_start_frame || cap.supports_end_frame);
  if (positional) {
    if (cap.supports_start_frame) {
      if (n === 1) return "start frame";
      if (n === 2 && cap.supports_end_frame) return "end frame";
    } else if (n === 1) {
      return "end frame";
    }
  }
  return `${type} ${n}`;
}

/** Which slot group a reference belongs to; media the project no longer knows counts as an image. */
function refType(r: ReferenceOut): MediaType {
  return r.media?.type === "video" ? "video" : "image";
}


/** Where the bin screen was opened from, when it was opened to pick a reference for another bin. */
interface Accept {
  /** The bin that wants the reference. */
  forBin: string;
  /** An existing reference on that bin to swap the media of: a gallery tap previews a take, and
   * "use as reference" rebinds it, keeping its handle. Absent means bind a new one from the footer. */
  ref?: string;
  /** The handle of that reference, once the referencing bin has loaded. */
  handle?: string;
  /** The media that reference is bound to now: its tile wears the grey "current" dot. */
  current?: string;
  /** Where to return once chosen; absent means the referencing bin itself. */
  back?: string;
}

interface BinBodyProps {
  slug: string;
  data: BinDetail;
  wide: boolean;
  accept: Accept | null;
  /** The recipe view: workflow, prompt, references and the rest, in place of the gallery's tags. */
  generate: boolean;
  /** The media to lead with when it is not the bin's own selection: the reference being re-picked. */
  lead?: string;
  /** Renders in flight on this bin, oldest first: each gets a placeholder in the strip. */
  jobs: JobOut[];
  onCancelJob: (jobId: string) => void;
  /** Generations saved for later on this bin, oldest first: each gets a waiting tile in the strip. */
  held: Generation[];
  onDiscardHeld: (genId: string) => void;
  /** The project's tag vocabulary, for the editor's suggestions. */
  projectTags: string[];
  onCommitTags: (tags: string[]) => void;
  onSelect: (id: string) => void;
  /** Make a take the bin's current one (the "tap to mark current" hint); absent while re-picking a reference. */
  onChoose?: (id: string) => void;
  /** The shown video started playing: it has really been looked at. */
  onPlayed: (id: string) => void;
  onDeleteMedia: (id: string) => void;
  onRecall: (id: string) => void;
  onCommitField: (field: "prompt" | "negative" | "workflow", value: string) => void;
  onCommitLength: (seconds: number) => void;
  onCommitLoras: (loras: Lora[]) => void;
  /** An empty slot was tapped: open the picker on that media type. */
  onAddReference: (type: MediaType) => void;
  /** A reference tile was tapped: the viewer opens on what it is bound to, ready to swap it. */
  onViewReference: (referenceId: string, mediaId: string) => void;
  onRemoveReference: (referenceId: string) => void;
  onReorderReferences: (order: string[]) => Promise<unknown>;
  agentLive: boolean;
}

function BinBody({ slug, data, wide, accept, generate, lead, jobs, onCancelJob, held, onDiscardHeld, projectTags, onCommitTags, agentLive, onSelect, onChoose, onPlayed, onDeleteMedia, onRecall, onCommitField, onCommitLength, onCommitLoras, onAddReference, onViewReference, onRemoveReference, onReorderReferences }: BinBodyProps) {
  const bin = data.bin;
  const workflows = useWorkflows();
  const images = (workflows.data ?? []).filter((w) => w.kind === "image");
  const videos = (workflows.data ?? []).filter((w) => w.kind === "video");
  const known = (workflows.data ?? []).some((w) => w.name === bin.workflow);
  const [promptDraft, setPromptDraft] = useState(bin.prompt);
  const [negativeDraft, setNegativeDraft] = useState(bin.negative);
  /** The prompt box itself: the `@` menu writes at its caret, and a focused box keeps its draft. */
  const promptRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    // a box being typed in keeps its draft: the server's word (a commit's trimmed copy, an
    // agent edit) lands on the next blur, not under the caret
    if (document.activeElement !== promptRef.current) setPromptDraft(bin.prompt);
    setNegativeDraft(bin.negative);
    // reruns only when the server's value actually changes, not on every background
    // refetch — so a same-value refetch mid-edit does not touch what the user is typing
  }, [bin.prompt, bin.negative]);
  // `media` arrives newest first; the selected one leads, else the newest
  const selected = data.media.find((m) => m.id === (lead ?? data.selected)) ?? data.media[0];
  // a render with no take yet (rendering, or saved for later) tapped in the strip: its recipe is
  // shown below like a take's, read only -- looked at, not recalled
  const [peek, setPeek] = useState<{ id: string; kind: "render" | "later" } | null>(null);
  const peekGone = !!peek && !(peek.kind === "render" ? jobs.some((j) => j.job === peek.id) : held.some((g) => g.id === peek.id));
  useEffect(() => {
    if (peekGone) setPeek(null); // it landed, or was discarded: the strip's own selection is back
  }, [peekGone]);
  const peeking = peek && !peekGone ? peek : null;
  // the gallery shows what made the selected take; cached per take, so stepping back is free
  const taken = useRecipe(slug, peeking ? peeking.id : generate ? null : selected?.id);
  // the strip scrolls sideways rather than wrapping; keep the selected media in view when it changes.
  // (The gallery's grid scrolls under its pinned picture: nothing to bring into view there.)
  const stripRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!selected || !generate) return;
    stripRef.current?.querySelector(`[data-media="${selected.id}"]`)?.scrollIntoView({ inline: "nearest", block: "nearest" });
  }, [selected?.id]);
  // Generating and saved-for-later tiles sit at the strip's left end: bring the strip there,
  // wherever it was, so they are in view without a hunt -- when the bin opens with some, and when
  // a new one starts or is saved. A refetch of the same ones does not move it.
  const pendingKey = [...jobs.map((j) => j.job), ...held.map((g) => g.id)].join(",");
  const pendingBefore = useRef<{ bin: string; key: string } | null>(null);
  useEffect(() => {
    const was = pendingBefore.current;
    pendingBefore.current = { bin: bin.id, key: pendingKey };
    if (!pendingKey) return;
    const opened = !was || was.bin !== bin.id; // this bin just came on screen
    const before = new Set(opened ? [] : was.key.split(","));
    if (opened || pendingKey.split(",").some((id) => id && !before.has(id))) {
      // after the selected take's own scroll-into-view (an effect above), so this one wins
      stripRef.current?.scrollTo({ left: 0, behavior: opened ? "auto" : "smooth" });
    }
  }, [pendingKey, bin.id]);
  /**
   * At the wide layout a long prompt scrolls inside its box instead of pushing References below
   * the fold: the prompt's cap is whatever the scroll box's height leaves after everything else
   * in the right column (label row, negative, references, gaps). Measured, not guessed, so the
   * references card can be one row or three, with or without a negative field; the textarea's
   * own resizer honours the resulting max-height (`--prompt-max`, read in base.css).
   */
  const leftRef = useRef<HTMLDivElement>(null);
  const rightRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const col = rightRef.current;
    const box = col?.closest(".screen-body") as HTMLElement | null;
    const ta = col?.querySelector<HTMLTextAreaElement>(".prompt-text");
    if (!wide || !generate || !col || !box || !ta) {
      rightRef.current?.style.removeProperty("--prompt-max");
      return;
    }
    const fit = () => {
      const pad = parseFloat(getComputedStyle(box).paddingTop) + parseFloat(getComputedStyle(box).paddingBottom);
      const others = col.getBoundingClientRect().height - ta.getBoundingClientRect().height; // fractional, so no rounding creep
      col.style.setProperty("--prompt-max", `${Math.max(120, Math.floor(box.clientHeight - pad - others))}px`);
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(box);
    col.querySelectorAll(":scope > .card").forEach((c) => ro.observe(c));
    return () => ro.disconnect();
  }, [wide, generate, data.references.length, data.workflow?.has_negative, data.capacity.image_slots, data.capacity.video_slots]);
  const cap = data.capacity;
  const totalSlots = cap.image_slots + cap.video_slots;
  const showRefs = totalSlots > 0 || data.references.length > 0;
  // references draw in the order they resolve: it names the imageN slots and numbers the handles
  const refsById = new Map(data.references.map((r) => [r.id, r]));
  /**
   * The slot grid: one group per media type, the workflow's slots laid out up front. A group
   * holds every reference of its type in bin order (usable, unused beyond the count, missing
   * files alike) followed by a `+` for each slot still open. Ordinals are per type in bin
   * order, so a group is one sortable list and dragging a tile onto a slot gives it that slot
   * and shifts the rest down; the last usable one drops to `unused` when the count is over.
   */
  const groups = (["image", "video"] as MediaType[])
    .map((type) => ({ type, refs: data.references.filter((r) => refType(r) === type), slots: slotsOf(cap, type) }))
    .filter((g) => g.slots > 0 || g.refs.length > 0);
  const orderAcross = (type: MediaType, order: string[]) => {
    const others = data.references.filter((r) => refType(r) !== type).map((r) => r.id);
    return type === "image" ? [...order, ...others] : [...others, ...order];
  };

  /**
   * The `@` menu. `mention` is derived from the caret, not toggled: every keystroke, click
   * and caret move asks "is the caret on an @ word?" — so backspacing the `@`, typing a space
   * or clicking elsewhere in the text all close it with no bookkeeping. Escape parks the one
   * `@` it was pressed on until the caret leaves it.
   */
  const refsCardRef = useRef<HTMLDivElement>(null);
  const [mention, setMention] = useState<Mention | null>(null);
  const [highlight, setHighlight] = useState(0);
  const dismissedAt = useRef<number | null>(null);
  const syncMention = useCallback(() => {
    const el = promptRef.current;
    if (!el) return;
    const m = findMention(el.value, el.selectionStart ?? el.value.length);
    if (!m) {
      dismissedAt.current = null;
      setMention(null);
      return;
    }
    if (dismissedAt.current === m.start) {
      setMention(null);
      return;
    }
    setMention((was) => (was && was.start === m.start && was.end === m.end && was.query === m.query ? was : m));
  }, []);
  const dismissMention = useCallback(() => {
    setMention((m) => {
      if (m) dismissedAt.current = m.start;
      return null;
    });
  }, []);
  // a rewrite (enhance, undo) lands without a caret event: re-ask
  useEffect(() => {
    if (mention) syncMention();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [promptDraft]);
  const mentionOptions: MentionOption[] = groups.flatMap((g) => {
    let n = 0;
    return g.refs.map((r) => {
      const ordinal = r.missing ? null : ++n;
      const slot = ordinal == null ? "missing" : ordinal > g.slots ? "unused" : slotLabel(cap, g.type, ordinal);
      return { handle: r.handle, mediaId: r.media?.id, mediaType: r.media?.type, slot, dim: ordinal == null || ordinal > g.slots };
    });
  });
  const matches = mention ? matchMentions(mentionOptions, mention.query) : [];
  useEffect(() => setHighlight(0), [mention?.query, mention?.start]);
  const pickMention = (handle: string) => {
    const el = promptRef.current;
    if (!el || !mention) return;
    const next = insertMention(el, mention, handle);
    setPromptDraft(next);
    // committed at once, not on blur: a tap that took focus for a moment has already committed
    // the text without the handle, and that answer must not win over this one
    if (next !== bin.prompt) onCommitField("prompt", next);
    dismissedAt.current = null;
    setMention(null);
  };
  /** The `@` chip: put an `@` at the caret (after a space when the caret follows a word) and let the menu derive. */
  const openMention = () => {
    const el = promptRef.current;
    if (!el) return;
    // a box nobody is in has no caret worth keeping: the name goes on the end
    const focused = document.activeElement === el;
    el.focus();
    const s = focused ? el.selectionStart ?? el.value.length : el.value.length;
    const e = focused ? el.selectionEnd ?? s : s;
    const needsSpace = s > 0 && !/[\s([{"'“‘]/.test(el.value[s - 1]);
    el.setRangeText(needsSpace ? " @" : "@", s, e, "end");
    setPromptDraft(el.value);
    dismissedAt.current = null;
    syncMention();
  };
  /** Leave the menu by taking the `@` word out of the prompt: Escape or Delete, nothing left behind. */
  const removeMention = () => {
    const el = promptRef.current;
    if (!el || !mention) return;
    el.setRangeText("", mention.start, mention.end, "end");
    el.focus();
    setPromptDraft(el.value);
    dismissedAt.current = null;
    setMention(null);
  };
  /**
   * The keys while the menu is up: arrows or Tab move the highlight (Tab never leaves the
   * box), Enter picks, Escape or Delete clear the `@` word and close. Backspace is the
   * browser's own: a character at a time, and taking the `@` itself closes the menu by
   * derivation.
   */
  const onMentionKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (!mention) return;
    if (e.key === "Escape" || e.key === "Delete") {
      e.preventDefault();
      removeMention();
      return;
    }
    if (!matches.length) return;
    const down = e.key === "ArrowDown" || (e.key === "Tab" && !e.shiftKey);
    const up = e.key === "ArrowUp" || (e.key === "Tab" && e.shiftKey);
    if (down || up) {
      e.preventDefault();
      setHighlight((h) => (h + (down ? 1 : matches.length - 1)) % matches.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      pickMention(matches[Math.min(highlight, matches.length - 1)].handle);
    }
  };
  const mentionOpen = mention !== null;
  const keepOpenWithin = [promptRef, refsCardRef];
  // the hero's pill: `take 3 · 1:23 to gen` when the take recorded how long ComfyUI took
  const took = selected ? genTime(selected) : null;
  const badge = selected ? `${mediaLabel(bin, selected)}${took ? ` · ${took} to gen` : ""}` : "";
  // takes that landed and nobody has looked at yet: ringed in the strip until shown
  const unseen = new Set(bin.unseen ?? []);
  const clearSeen = useClearBinSeen(slug);
  // a placeholder takes the shape of what is coming: the last take's, else the kind's
  const pendingRatio = Math.min(2, Math.max(0.5, ratioOf(selected) ?? (data.kind === "video" ? 16 / 9 : 1)));
  const picking = accept?.ref ? `@${accept.handle ?? "…"}` : "";
  const usedBy = picking ? ` · ${picking}` : "";

  // the selected take's size and quality, under the strip (the picture, in the gallery): what to
  // compare while stepping through takes, without moving the picture or the strip
  const facts = peeking ? (
    <div className="take-facts" aria-label="This generation's size and quality">
      <span className="take-facts-info">
        {taken.data?.width && taken.data?.height ? (
          <span>{taken.data.quarter ? `${Math.floor(taken.data.width / 2)}×${Math.floor(taken.data.height / 2)}` : `${taken.data.width}×${taken.data.height}`}</span>
        ) : null}
        {taken.data?.draft ? <span className="is-draft">draft</span> : null}
        {!generate && taken.data?.found && taken.data.workflow ? (
          <span className="take-facts-workflow" title={taken.data.workflow}>
            {workflowLabel(taken.data.workflow)}
          </span>
        ) : null}
        {!generate && taken.data?.length_s != null ? <span>{`${Math.round(taken.data.length_s * 100) / 100} s`}</span> : null}
      </span>
      <span className="take-facts-state">{peeking.kind === "later" ? "saved for later" : "generating"}</span>
    </div>
  ) : selected && (
    <div className="take-facts" aria-label="This take's size and quality">
      {/* the take's own label is on the picture and the strip already; this line is the facts
          to compare, and the state cell holds its place whichever way it reads, so marking a
          take current (or stepping to one that is not) moves nothing on screen */}
      <span className="take-facts-info">
        {selected.width && selected.height ? <span>{`${selected.width}×${selected.height}`}</span> : null}
        {taken.data?.draft ? <span className="is-draft">draft</span> : null}
        {!generate && taken.data?.found && taken.data.workflow ? (
          <span className="take-facts-workflow" title={taken.data.workflow}>
            {workflowLabel(taken.data.workflow)}
          </span>
        ) : null}
      </span>
      {onChoose && (
        <span className="take-facts-state">
          {selected.id === bin.selected ? (
            <span className="take-winner" title="the bin's current take: what references to this bin and the library lead with">
              <span className="take-winner-dot" aria-hidden="true" /> current
            </span>
          ) : (
            <button type="button" className="take-choose" onClick={() => onChoose(selected.id)} title="make this the bin's current take: what references to this bin and the library lead with">
              tap to mark current
            </button>
          )}
        </span>
      )}
    </div>
  );

  // the picture: in the gallery it stays pinned at the top while the facts and the grid scroll under it
  const hero = (
    <>
      {peeking ? (
          <div
            className="preview is-peek"
            style={{ "--ar": taken.data?.width && taken.data?.height ? taken.data.width / taken.data.height : pendingRatio } as CSSProperties}
          >
            <span>{peeking.kind === "later" ? "saved for later — not generated yet" : "generating — no take yet"}</span>
          </div>
        ) : selected ? (
          <TakePreview slug={slug} media={selected} badge={`${badge}${usedBy}`} onPlayed={() => onPlayed(selected.id)} />
        ) : (
          <div className="preview-empty">{jobs.length ? "generating…" : "nothing here yet — Generate or Upload below"}</div>
        )}
    </>
  );

  // the takes: a strip under the picture while generating, a grid under the facts in the gallery
  const gallery = (
    <>
        <div className="row">
          <span className="tag">GALLERY ({data.media.length})</span>
          {jobs.length > 0 && <span className="tag is-live">{jobs.length === 1 ? "generating" : `generating ×${jobs.length}`}</span>}
          {held.length > 0 && <span className="tag is-held" title="waiting to generate — start the group from the bar">{held.length} later</span>}
          {unseen.size > 0 && <span className="tag is-new">{unseen.size} new</span>}
          {unseen.size > 0 && (
            <button type="button" className="tag-link" onClick={() => clearSeen.mutate(bin.id)} title="mark everything in this bin as seen">
              clear
            </button>
          )}
          <span className="spacer" />
        </div>
        <div className={`row thumbs-row takes-strip ${generate ? "" : "is-grid"}`} ref={stripRef}>
          {jobs
            .slice()
            .reverse()
            .map((j) => (
              <div key={j.job} className="thumb-col is-pending" data-job={j.job}>
                <div
                  className={`thumb is-pending is-tappable ${peeking?.id === j.job ? "is-selected" : ""}`}
                  style={generate ? { width: Math.round(64 * pendingRatio), height: 64 } : { width: "100%", aspectRatio: String(pendingRatio) }}
                  role="button"
                  tabIndex={0}
                  aria-label={`generating: ${jobPhaseLine(j) ?? "queued"} — show its recipe`}
                  title={`${jobPhaseLine(j) ?? "generating…"} — tap to see its recipe`}
                  onClick={() => setPeek({ id: j.job, kind: "render" })}
                  onKeyDown={(e) => e.key === "Enter" && setPeek({ id: j.job, kind: "render" })}
                >
                  <span className="thumb-shimmer" aria-hidden="true" />
                </div>
                <span className="take-num is-pending" aria-hidden="true">
                  {jobStepLabel(j) ?? "…"}
                </span>
                {generate && (
                  <button
                    type="button"
                    className="take-recall"
                    aria-label="Recall the settings of this generation"
                    title="recall these settings — the bin goes back to this recipe; this generation stays as it is"
                    onClick={(e) => {
                      e.stopPropagation();
                      onRecall(j.job);
                    }}
                  >
                    ⟳
                  </button>
                )}
                <button
                  type="button"
                  className="take-remove"
                  aria-label="Cancel this generation"
                  title="cancel this generation"
                  onClick={(e) => {
                    e.stopPropagation();
                    onCancelJob(j.job);
                  }}
                >
                  ✕
                </button>
              </div>
            ))}
          {held
            .slice()
            .reverse()
            .map((g) => (
              <div key={g.id} className="thumb-col is-held" data-held={g.id}>
                <div
                  className={`thumb is-held is-tappable ${peeking?.id === g.id ? "is-selected" : ""}`}
                  style={generate ? { width: Math.round(64 * pendingRatio), height: 64 } : { width: "100%", aspectRatio: String(pendingRatio) }}
                  role="button"
                  tabIndex={0}
                  aria-label="waiting to generate — show its recipe"
                  title="generates later — tap to see its recipe"
                  onClick={() => setPeek({ id: g.id, kind: "later" })}
                  onKeyDown={(e) => e.key === "Enter" && setPeek({ id: g.id, kind: "later" })}
                >
                  {LATER_GLYPH}
                </div>
                <span className="take-num is-held" aria-hidden="true">
                  later
                </span>
                {generate && (
                  <button
                    type="button"
                    className="take-recall"
                    aria-label="Recall the settings of this saved generation"
                    title="recall these settings — the bin goes back to this recipe; this saved generation stays as it is"
                    onClick={(e) => {
                      e.stopPropagation();
                      onRecall(g.id);
                    }}
                  >
                    ⟳
                  </button>
                )}
                <button
                  type="button"
                  className="take-remove"
                  aria-label="Discard this waiting generation"
                  title="discard — it will not be generated"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDiscardHeld(g.id);
                  }}
                >
                  ✕
                </button>
              </div>
            ))}
          {data.media.map((m) => (
            <div key={m.id} className="thumb-col" data-media={m.id}>
              <MediaThumb
                slug={slug}
                id={m.id}
                type={m.type}
                duration={m.duration_s}
                size={64}
                ratio={ratioOf(m)}
                fill={!generate}
                selected={!peeking && m.id === selected?.id}
                fresh={unseen.has(m.id)}
                // the grey dot marks the winner (or, re-picking, the reference's take); a landed take
                // still unseen keeps its blue dot until it has been looked at
                current={accept?.ref ? m.id === accept.current : m.id === bin.selected && !unseen.has(m.id)}
                label={mediaLabel(bin, m)}
                title={wide && genTime(m) ? `${mediaLabel(bin, m)}
${genTime(m)} to generate` : undefined}
                onClick={() => {
                  // back from looking at a render: the first tap only returns to this take
                  if (peeking) {
                    setPeek(null);
                    if (m.id === selected?.id) return;
                  }
                  onSelect(m.id);
                }}
              />
              <span className="take-num" aria-hidden="true">
                {mediaLabel(bin, m).replace("take ", "")}
              </span>
              {generate && m.origin?.type === "generation" && (
                <button
                  type="button"
                  className="take-recall"
                  aria-label={`Recall the settings that made ${mediaLabel(bin, m)}`}
                  title="recall these settings — Generate for another take"
                  onClick={(e) => {
                    e.stopPropagation();
                    onRecall(m.id);
                  }}
                >
                  ⟳
                </button>
              )}
              <button
                type="button"
                className="take-remove"
                aria-label={`Delete ${mediaLabel(bin, m)}`}
                title="delete this media everywhere"
                onClick={(e) => {
                  e.stopPropagation();
                  onDeleteMedia(m.id);
                }}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
    </>
  );

  return (
    <div className={`shot-body ${wide ? "is-wide" : ""} ${generate ? "" : "is-gallery"}`}>
      <div className="shot-left" ref={leftRef}>
        {generate ? (
          hero
        ) : (
          <div className="preview-pin">
            {hero}
            {facts}
          </div>
        )}
        {generate && gallery}
        {generate && facts}
        {/* an upload's provenance sits under the strip, never between it and the picture: stepping
            through takes must keep the picture and the strip exactly where they are, so a
            discontinuity shows as the only thing that changed */}
        {!peeking && selected?.origin?.note && (
          <div className="take-note">
            <span className="take-note-label">from {selected.origin.source ?? "a file"}</span>
            <span className="take-note-text">{selected.origin.note}</span>
          </div>
        )}
        {picking && <Hint>tap a take to preview it · grey dot = what {picking} uses now · “use as reference” swaps it (same name in the prompt) and goes back</Hint>}

        {!generate && (
          <div className="gallery-facts">
            <div className="card gallery-tags">
              <div className="tag">TAGS</div>
              <TagEditor tags={bin.tags ?? []} suggestions={projectTags} onChange={onCommitTags} />
            </div>
            {peeking && taken.data?.found && (
              <div className="card gallery-recipe">
                <div className="tag">PROMPT</div>
                <div className="fact-value fact-prompt veil-text veil-read" tabIndex={0}>
                  {taken.data.prompt || <span className="fact-dim">none</span>}
                </div>
                {taken.data.references.length > 0 && (
                  <div className="row wrap gallery-recipe-refs">
                    {taken.data.references.map((id, i) => (
                      <span key={id} className="shot-ref">
                        <MediaThumb slug={slug} id={id} size={48} />
                        <span className="shot-ref-label">@{taken.data!.handles[i] ?? id}</span>
                      </span>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
        {!generate && gallery}

        {generate && (
        <div className="shot-controls">
          <div className="card card-strong">
            <div className="tag">WORKFLOW</div>
            <select
              className="field-select"
              value={bin.workflow}
              onChange={(e) => onCommitField("workflow", e.target.value)}
              aria-label="Workflow"
            >
              {!bin.workflow && <option value="">choose a workflow…</option>}
              {!known && bin.workflow && <option value={bin.workflow}>{workflowLabel(bin.workflow)}</option>}
              <optgroup label="video">
                {videos.map((w) => (
                  <option key={w.name} value={w.name}>
                    {workflowLabel(w.name)}
                  </option>
                ))}
              </optgroup>
              <optgroup label="image">
                {images.map((w) => (
                  <option key={w.name} value={w.name}>
                    {workflowLabel(w.name)}
                  </option>
                ))}
              </optgroup>
            </select>
          </div>

          {data.kind === "video" && (
            <div className="card">
              <div className="tag">LENGTH</div>
              <LengthField value={bin.length_s != null ? Number(bin.length_s) : null} onChange={onCommitLength} />
            </div>
          )}
        </div>
        )}

        {generate && (data.workflow?.has_lora || (bin.loras ?? []).length > 0) && (
          <LoraRow loras={bin.loras ?? []} supported={!!data.workflow?.has_lora} onChange={onCommitLoras} />
        )}
      </div>

      {generate && (
      <div className="shot-right" ref={rightRef}>
        <div className="card">
          <EnhancePrompt
            label={<div className="card-label">prompt</div>}
            slug={slug}
            kind={data.kind ?? "image"}
            workflow={bin.workflow}
            value={promptDraft}
            references={data.references.map((r) => ({ handle: r.handle, type: r.media?.type ?? "image" }))}
            agentLive={agentLive}
            storageKey={bin.id}
            binId={bin.id}
            onReplace={(p) => {
              // committed at once, not on blur: a phone that backgrounds the tab must not lose it
              setPromptDraft(p);
              onCommitField("prompt", p);
            }}
          >
            <AutoTextarea
              inputRef={promptRef}
              className="field-input prompt-text veil-text"
              maxRows={40}
              value={promptDraft}
              onChange={(v) => {
                setPromptDraft(v);
                // the box already holds the new text and caret when the change is reported
                syncMention();
              }}
              onSelect={syncMention}
              onClick={syncMention}
              onKeyUp={syncMention}
              onKeyDown={onMentionKey}
              onBlur={() => promptDraft !== bin.prompt && onCommitField("prompt", promptDraft)}
              placeholder="type @ to name a reference…"
            />
            {mentionOpen && slug && (
              <RefMention
                slug={slug}
                options={matches}
                highlight={highlight}
                onPick={pickMention}
                onHover={setHighlight}
                keepOpenWithin={keepOpenWithin}
                onDismiss={dismissMention}
              />
            )}
          </EnhancePrompt>
        </div>

        {data.workflow?.has_negative && (
          <div className="card">
            <div className="tag">NEGATIVE</div>
            <AutoTextarea
              className="field-input is-negative negative-text veil-text"
              value={negativeDraft}
              onChange={setNegativeDraft}
              onBlur={() => negativeDraft !== bin.negative && onCommitField("negative", negativeDraft)}
              placeholder="none"
            />
          </div>
        )}

        {showRefs && (
          <div className={`card card-strong refs-card ${mentionOpen ? "is-picking" : ""}`} ref={refsCardRef}>
            <div className="row">
              <span>
                References ({data.references.length}/{totalSlots})
              </span>
              {data.references.length > 0 && !mentionOpen && (
                <button
                  type="button"
                  className="chip ref-at"
                  title="name a reference in the prompt"
                  aria-label="Insert a reference name"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={openMention}
                >
                  @
                </button>
              )}
              <span className="spacer" />
              {mentionOpen && <span className="hint">tap a tile to name it in the prompt</span>}
            </div>
            <div className="row refs-row wrap">
              {groups.map((g) => {
                // slots the workflow still has open for this type, after the files that are there
                const filled = g.refs.filter((r) => !r.missing).length;
                const open = Array.from({ length: Math.max(0, g.slots - filled) }, (_, i) => filled + i + 1);
                return (
                  <div key={g.type} className="refs-group">
                    <SortableList ids={g.refs.map((r) => r.id)} onCommit={(order) => onReorderReferences(orderAcross(g.type, order))} layout="grid">
                      {(order) => {
                        // the slot a tile wears follows the live drag order, not the server's last word
                        let n = 0;
                        return order.map((id) => {
                          const r = refsById.get(id);
                          if (!r) return null;
                          const ordinal = r.missing ? null : ++n;
                          const unused = ordinal != null && ordinal > g.slots;
                          const slot = ordinal == null ? "missing" : unused ? "unused" : slotLabel(cap, g.type, ordinal);
                          return (
                            <SortableItem key={r.id} id={r.id}>
                              {({ item, handle, isDragging }) => (
                                <div
                                  className={`ref-tile ${r.missing ? "is-missing" : ""} ${unused ? "is-unused" : ""} ${isDragging ? "is-dragging" : ""} ${mentionOpen ? "is-pickable" : ""}`}
                                  title={mentionOpen ? `put @${r.handle} in the prompt` : r.media?.path ?? "missing"}
                                  {...item}
                                  // the tile is its own grip; while the @ menu is up it is a pick target
                                  // instead: no drag, and no blur that would drop the caret or the keyboard
                                  {...(mentionOpen ? { onMouseDown: (e: React.MouseEvent) => e.preventDefault() } : handle)}
                                  ref={(el) => {
                                    item.ref(el);
                                    handle.ref(el);
                                  }}
                                >
                                  <MediaThumb
                                    slug={slug}
                                    id={r.media?.id}
                                    type={r.media?.type}
                                    duration={r.media?.duration_s}
                                    size={68}
                                    ratio={ratioOf(r.media)}
                                    dim={unused}
                                    dashed={r.missing}
                                    onClick={mentionOpen ? () => pickMention(r.handle) : r.media ? () => onViewReference(r.id, r.media!.id) : undefined}
                                  />
                                  {!mentionOpen && (
                                    <button type="button" className="ref-remove" aria-label="Remove reference" onClick={() => onRemoveReference(r.id)}>
                                      ✕
                                    </button>
                                  )}
                                  <span className="ref-label">@{r.handle}</span>
                                  <span className="ref-slot">{slot}</span>
                                </div>
                              )}
                            </SortableItem>
                          );
                        });
                      }}
                    </SortableList>
                    {open.map((n) => {
                      const slot = slotLabel(cap, g.type, n);
                      return (
                        <div key={`open-${g.type}-${n}`} className="ref-tile is-empty">
                          <button type="button" className="tile-add" aria-label={`Add ${slot} reference`} title={`add a ${g.type} reference · or paste an image (Ctrl+V)`} disabled={mentionOpen} onClick={() => onAddReference(g.type)}>
                            +
                          </button>
                          <span className="ref-slot">{slot}</span>
                        </div>
                      );
                    })}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
      )}
    </div>
  );
}

export function BinRoute() {
  const { slug, binId } = useParams();
  const navigate = useNavigate();
  const { wide, xwide } = useBreakpoint();
  const libPicker = useLibraryPicker();
  const detail = useBin(slug, binId);
  const data = detail.data;
  const binPath = (id: string) => `/p/${encodeURIComponent(slug ?? "")}/b/${encodeURIComponent(id)}`;

  /**
   * Query string state, all applied once and then stripped or honoured on the way out:
   *   `?generate` the recipe view. A bin always opens on its gallery; Generate ▸ adds this,
   *     ‹ Gallery drops it, and recalling a take's settings lands here so they can be seen.
   *   `?recall=<media>` restores the settings that made one of this bin's takes.
   *   `?for=<bin>&back=<path>` this bin was made from another bin's picker: the footer offers
   *     to bind the selected media there and go back.
   *   `?for=<bin>&ref=<reference>&back=<path>` opened by tapping a reference tile: a gallery
   *     tap only previews (the hero follows it; this bin's own selection is left alone), the
   *     bound take wears a grey "current" dot, and "use as reference" rebinds the reference
   *     (same handle) and returns to the referencing bin's recipe.
   */
  const [params] = useSearchParams();
  const generating = params.has("generate");
  /** The same screen with the recipe shown or hidden; every other param rides along. */
  const withMode = (on: boolean) => {
    const next = new URLSearchParams(params);
    if (on) next.set("generate", "");
    else next.delete("generate");
    const q = next.toString();
    return `${binPath(binId ?? "")}${q ? `?${q}` : ""}`;
  };
  const recallId = params.get("recall") ?? undefined;
  const forBin = params.get("for");
  const refId = params.get("ref") ?? undefined;
  const forDetail = useBin(slug, refId ? forBin ?? undefined : undefined);
  const bound = refId ? forDetail.data?.references.find((r) => r.id === refId) : undefined;
  const accept: Accept | null = forBin
    ? { forBin, ref: refId, handle: bound?.handle, current: bound?.media?.id, back: params.get("back") ?? undefined }
    : null;
  const acceptReturn = accept ? accept.back ?? binPath(accept.forBin) : null;
  // re-picking a reference: the tapped media leads here until the referencing bin reports it
  const [picked, setPicked] = useState<string | null>(null);
  // looking is not choosing: a tap shows a take here (local), a second tap on the one shown marks
  // it the bin's current take (`selected` on the record: what references and the library lead with)
  const [viewing, setViewing] = useState<string | null>(null);
  const lead = refId ? picked ?? bound?.media?.id ?? undefined : viewing ?? undefined;
  // the bound media leads even when it lives outside this gallery (a handle keeps its bin's
  // name after a rebind): never fall back to this bin's own pick while re-picking
  const selected = data
    ? data.media.find((m) => m.id === (lead ?? data.selected)) ?? (lead && bound?.media?.id === lead ? bound.media : undefined) ?? data.media[0]
    : undefined;
  const recipe = useRecipe(slug, recallId);
  const [recalledId, setRecalledId] = useState<string | null>(null); // applied once per take asked for
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  // the reference-first path: the selected media into a bin's recipe, this one by default
  const [startFrom, setStartFrom] = useState(false);

  const generate = useGenerate(slug, binId);
  // every live render on this bin, oldest first: the project's live list (event-fed) plus what
  // the detail reports, so a detail refetch mid-render never blinks a placeholder away
  const liveJobs = useLiveJobs(slug);
  const jobs = liveJobsFor(binId, liveJobs.data, data?.active_jobs);
  const busy = jobs.length > 0;
  const finished = (data as LiveBinDetail | undefined)?.finished_job ?? null;
  const showToast = useToast();
  const toasted = useRef("");

  const project = useProject(slug);
  const session = project.data?.agent_session ?? null;
  const createBin = useCreateBin(slug, project.data?.bins);
  const cloneBin = useCloneBin(slug);
  useEffect(() => {
    if (!finished) return;
    const key = `${finished.job}:${finished.status}`;
    if (toasted.current === key) return;
    toasted.current = key;
    if (finished.status === "done") {
      // the take that just landed is what to look at next -- shown, not chosen: the winner moves
      // only on a second tap (re-picking a reference: it is the preview)
      const newest = finished.takes[finished.takes.length - 1];
      if (newest && accept?.ref) setPicked(newest);
      else if (newest && finished.bin === binId) setViewing(newest);
      const what = finished.takes.length === 1 ? "take generated" : `${finished.takes.length} takes generated`;
      const took = execTimeLabel(finished.exec_seconds);
      showToast(took ? `${what} · ${took} to gen` : what);
    }
    else if (finished.status === "failed") showToast(`generation failed — ${firstLine(finished.error)}`);
    else if (finished.status === "cancelled") showToast(`generation cancelled — ${firstLine(finished.error)}`);
    // showToast is stable enough for this: a new closure only re-arms the same timer
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finished]);

  // looked at, not merely shown: the new-take marker stays while the take is only selected. An
  // image's comes off when the user moves on from it (another take, another screen); a video's
  // when it plays. A take that lands selected and is never looked at keeps its dot.
  const markSeen = useMarkSeen(slug);
  const { mutate: seen } = markSeen;
  const unseenRef = useRef<string[]>([]);
  unseenRef.current = data?.bin.unseen ?? [];
  const seenIfNew = useCallback(
    (id: string) => {
      if (unseenRef.current.includes(id)) seen(id);
    },
    [seen],
  );
  const shownId = selected?.id;
  const shownType = selected?.type;
  useEffect(() => {
    if (!shownId || shownType !== "image") return;
    // decided now, while this bin's list is the one in hand: by the time the cleanup runs on a
    // move to another bin, the ref already holds that bin's list and would miss this image
    const wasNew = unseenRef.current.includes(shownId);
    // moved on: another selection, another bin, or another screen. Shown is seen; the bin's
    // other new takes keep their dots (they were never on screen), and the bin's order by
    // recency is untouched (marking seen is not a change)
    return () => {
      if (wasNew) seen(shownId);
    };
  }, [shownId, shownType, seen]);

  const [quarter] = useQuarterSize();
  const [draft] = useDraft();
  const [later] = useLater();
  const onGenerate = () => {
    void requestNotifications(); // first Generate is the user gesture that may ask; a no-op once answered
    lockVeil(); // a private project's pictures go back under the veil the moment a render is asked for
    generate.mutate(
      {
        params: {
          ...(data?.kind === "video" && data.capacity.supports_size ? quarterParams(quarter) : {}),
          ...draftParams(draft, !!data?.capacity?.supports_draft),
        },
        hold: later,
      },
      {
        onSuccess: () => later && showToast("generates later — start the group from the bar when you're done"),
        onError: (err) => showToast(errorText(err)),
      },
    );
  };
  const cancelJob = useCancelJob(slug);
  const onCancelJob = (jobId: string) => cancelJob.mutate(jobId, { onError: (err) => showToast(errorText(err)) });
  // saved for later on this bin, oldest first; the project's list, filtered
  const heldAll = useHeld(slug);
  const held = (heldAll.data ?? []).filter((g) => g.bin === binId);
  const discardHeld = useDiscardHeld(slug);
  const onDiscardHeld = (genId: string) =>
    discardHeld.mutate(genId, { onSuccess: () => showToast("discarded"), onError: (err) => showToast(errorText(err)) });

  const command = useCommand(slug);
  useEffect(() => {
    if (!recallId || recalledId === recallId || !recipe.data || !binId) return;
    setRecalledId(recallId);
    const r = recipe.data;
    // recalled settings are for looking at: land on the recipe
    const strip = () => navigate(`${binPath(binId)}?generate`, { replace: true });
    if (!r.found) {
      showToast("nothing to recall — that one was uploaded, not generated");
      navigate(binPath(binId), { replace: true });
      return;
    }
    command.mutate(
      {
        name: "recall_generation",
        body: {
          bin: binId,
          from_media: recallId,
          workflow: r.workflow_missing ? "" : r.workflow,
          prompt: r.prompt,
          negative: r.negative,
          references: r.references.map((media, i) => ({ media, handle: r.handles[i] ?? media })),
          loras: r.loras ?? [],
        },
      },
      {
        onSuccess: () => showToast(r.missing.length ? `recalled — ${r.missing.length} reference(s) since deleted` : "recalled — Generate for another take"),
        onError: (e) => showToast(errorText(e)),
      },
    );
    strip();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recallId, recalledId, recipe.data, binId]);

  /** A media record: deleting it cascades out of every bin, reference and gallery. The file
   * ComfyUI wrote, or the upload, stays on disk unless nothing else used it. */
  const onDeleteMedia = (id: string) => {
    setPendingDelete(null);
    command.mutate(
      { name: "delete_media", body: { media: id } },
      { onSuccess: (r) => showToast(r.change?.description ?? "deleted"), onError: (err) => showToast(errorText(err)) },
    );
  };

  // the bin's own choice; none until someone chooses (the gallery then leads with the newest)
  const winner = data?.bin.selected ?? null;
  // a new winner (this tap, a landing take, the agent) or another bin: the view follows the winner
  useEffect(() => {
    setViewing(null);
  }, [winner, binId]);
  const choose = (id: string) =>
    command.mutate(
      { name: "set_bin_field", body: { bin: binId, field: "selected", value: id } },
      { onError: (err) => showToast(errorText(err)) },
    );
  /** A tap on a take: the first shows it, a tap on the one already shown makes it the winner. */
  const onSelect = (id: string) => {
    if (id === selected?.id) {
      if (id !== winner) choose(id);
    } else setViewing(id);
  };

  /** Re-picking a reference: a tap only previews; nothing is bound until "use as reference". */
  const onPickForReference = (id: string) => {
    if (accept?.ref) setPicked(id);
  };

  /** Bind the previewed take to the reference (same handle) and go back to the recipe that uses it. */
  const onUseAsReference = () => {
    if (!accept?.ref || !data || !lead || !acceptReturn) return;
    const media = data.media.find((m) => m.id === lead);
    command.mutate(
      { name: "rebind_reference", body: { bin: accept.forBin, reference: accept.ref, media: lead } },
      {
        onSuccess: (r) => {
          showToast(r.change?.description ?? `@${accept.handle ?? "reference"} is now ${media ? mediaLabel(data.bin, media) : lead}`);
          navigate(acceptReturn, { replace: true });
        },
        onError: (err) => showToast(errorText(err)),
      },
    );
  };

  const onCommitField = (field: "name" | "prompt" | "negative" | "workflow", value: string) =>
    command.mutate({ name: "set_bin_field", body: { bin: binId, field, value } }, { onError: (err) => showToast(errorText(err)) });

  const onCommitLength = (seconds: number) =>
    command.mutate({ name: "set_bin_field", body: { bin: binId, field: "length_s", value: seconds } }, { onError: (err) => showToast(errorText(err)) });

  const onCommitLoras = (loras: Lora[]) =>
    command.mutate({ name: "set_bin_field", body: { bin: binId, field: "loras", value: loras } }, { onError: (err) => showToast(errorText(err)) });

  const setTags = useSetBinTags(slug, binId);
  const onCommitTags = (tags: string[]) => setTags.mutate(tags, { onError: (err) => showToast(errorText(err)) });

  const onBindReference = (mediaId: string) =>
    command.mutate(
      { name: "bind_reference", body: { bin: binId, media: mediaId } },
      {
        onSuccess: () => {
          showToast("bound as a reference");
          libPicker.closePicker();
        },
        onError: (err) => showToast(errorText(err)),
      },
    );

  const onRemoveReference = (referenceId: string) =>
    command.mutate(
      { name: "unbind_reference", body: { bin: binId, reference: referenceId } },
      { onSuccess: () => showToast("removed from this generation — media stays in its bin"), onError: (err) => showToast(errorText(err)) },
    );

  const onReorderReferences = (order: string[]) =>
    command.mutateAsync({ name: "reorder_references", body: { bin: binId, order } }).catch((err) => {
      showToast(errorText(err));
      throw err;
    });

  // tapping a reference tile opens the viewer on what it is bound to, paging that media's bin;
  // from there it swaps the reference in place, or opens the whole bin in the picking flow
  const mediaViewer = useMediaViewer();
  const onViewReference = (referenceId: string, mediaId: string) =>
    slug && binId && mediaViewer.open(slug, mediaId, false, { [VIEW_FOR_PARAM]: binId, [VIEW_REF_PARAM]: referenceId });

  /** The accept action for a bin made from another bin's picker: bind this bin's selected media there, then go back. */
  const onAccept = () => {
    if (!accept || !selected || !acceptReturn) return;
    command.mutate(
      { name: "bind_reference", body: { bin: accept.forBin, media: selected.id } },
      {
        onSuccess: (r) => {
          showToast(r.change?.description ?? "bound as a reference");
          navigate(acceptReturn, { replace: true });
        },
        onError: (err) => showToast(errorText(err)),
      },
    );
  };

  // Upload ▸ into this bin; the newest arrival leads
  const upload = useUploadMany(slug);
  const flip = useFlipMedia(slug);
  const [uploadDone, setUploadDone] = useState(0);
  const [uploadTotal, setUploadTotal] = useState(0);
  const fileRef = useRef<HTMLInputElement>(null);
  const uploadHere = (files: File[]) => {
    if (!files.length || !binId) return;
    setUploadDone(0);
    setUploadTotal(files.length);
    upload.mutate(
      { files, bin: binId, onProgress: setUploadDone },
      {
        onSuccess: (res) => {
          // the new arrival is what to look at next -- shown, not chosen, the same as a fresh take
          // (re-picking a reference: it is the preview)
          const last = res.uploaded[res.uploaded.length - 1]?.media?.id;
          if (accept?.ref && last) setPicked(last);
          else if (last) setViewing(last);
          showToast(uploadSummary(res, data?.bin.name));
        },
        onError: (e) => showToast(errorText(e)),
      },
    );
  };
  const onUploadPicked = (e: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    uploadHere(files);
  };
  /**
   * A paste on the bin screen is an upload with no browse. In the gallery it lands here like
   * ⇪ Upload does. In the recipe view with a reference slot still open it is a reference: the
   * sheet asks which bin the picture belongs to (this one by default), then binds it. The
   * picker and an open sheet take their own pastes first; the agent composer takes its own.
   */
  const [pastedForRef, setPastedForRef] = useState<File[] | null>(null);
  const openSlots = data ? data.capacity.image_slots + data.capacity.video_slots - data.references.length : 0;
  usePasteFiles(
    (files, e) => {
      if (pastedInAgentPanel(e) || !data || !binId) return false;
      if (generating && openSlots > 0) setPastedForRef(files);
      else uploadHere(files);
    },
    !libPicker.open && !startFrom && !pastedForRef,
  );

  const canClip = selected?.type === "video";
  const pendingLabel = pendingDelete && data ? mediaLabel(data.bin, data.media.find((m) => m.id === pendingDelete)!) : "";
  /**
   * Where you are: `Memes ‹ newtoold`, and `‹ Generate` in the recipe view, where the bin's name
   * becomes the way back to its gallery. Opened to pick a reference for another bin, that bin
   * sits in the trail too, so there is a way back without picking. The name renames only in the
   * gallery, where a tap has nothing else to do.
   */
  const binName = data?.bin.name ?? "…";
  // cards about this bin still waiting on a verdict (from the feed's cache; nothing fetched twice)
  const feedEntries = useFeed(slug).data?.entries ?? [];
  const openCards = feedEntries.filter((e) => openCardAbout(e, binId)).map((e) => e.change!.id);
  const ui = useAgentUi();
  const sessionPanel = useSessionPanel();
  const crumbs: Crumb[] = [{ label: project.data?.name ?? slug ?? "…", to: `/p/${encodeURIComponent(slug ?? "")}`, title: "this project" }, libraryCrumb(slug, "bins")];
  if (accept && acceptReturn) {
    crumbs.push({ label: project.data?.bins.find((b) => b.id === accept.forBin)?.name ?? "…", to: acceptReturn, title: "back without picking" });
  }
  if (generating) crumbs.push({ label: binName, to: binPath(binId ?? ""), title: "this bin's gallery" }, { label: "Generate" });
  else crumbs.push({ label: binName, rename: { onSave: (name) => onCommitField("name", name), ariaLabel: "Bin name", disabled: !data } });

  return (
    <Shell slug={slug} binId={binId}>
      <div className="screen" data-screen-label="Bin">
        <header className="screen-head">
          {/* the agent already asked about this bin: read that card before a note here undoes it */}
          {openCards.length > 0 && (
            <button
              type="button"
              className="chip bin-card-waiting"
              onClick={() => {
                if (xwide) ui.setRailOpen(true);
                else sessionPanel.openPanel();
                requestJump(openCards[0]);
              }}
              title="a card about this bin is waiting in the agent panel — open it"
            >
              ⚠ {openCards.length === 1 ? "card waiting" : `${openCards.length} cards waiting`}
            </button>
          )}
          <Crumbs items={crumbs} />
          <span className="spacer" />
          {!accept && (
            <button
              type="button"
              className="link"
              disabled={!data || cloneBin.pending}
              onClick={() => data && cloneBin.clone(data.bin)}
              title="a new bin with this recipe and its references, and no media"
            >
              {cloneBin.pending ? "cloning…" : "⧉ clone"}
            </button>
          )}
        </header>
        <div className="screen-body">
          {detail.isLoading && <div className="hint">loading…</div>}
          {detail.error && <div className="hint">could not load this bin</div>}
          {data && slug && (
            <BinBody
              slug={slug}
              data={data}
              wide={wide}
              accept={accept}
              generate={generating}
              lead={lead}
              jobs={jobs}
              onCancelJob={onCancelJob}
              held={held}
              onDiscardHeld={onDiscardHeld}
              projectTags={project.data?.tags ?? []}
              onCommitTags={onCommitTags}
              onSelect={accept?.ref ? onPickForReference : onSelect}
              onChoose={accept?.ref ? undefined : choose}
              onPlayed={seenIfNew}
              onDeleteMedia={setPendingDelete}
              onRecall={(id) => navigate(`${binPath(binId ?? "")}?recall=${encodeURIComponent(id)}`)}
              onCommitField={onCommitField}
              onCommitLength={onCommitLength}
              onCommitLoras={onCommitLoras}
              onAddReference={(type) => libPicker.openPicker("doc", type)}
              onViewReference={onViewReference}
              onRemoveReference={onRemoveReference}
              onReorderReferences={onReorderReferences}
              agentLive={session?.status === "live"}
            />
          )}
          {data && slug && !generating && <PostsStrip slug={slug} bin={data.bin} />}
        </div>
        <footer className="screen-foot is-split">
          {generating ? (
            <button type="button" className="btn-outline" onClick={() => navigate(withMode(false))} title="back to this bin's gallery">
              ‹ Gallery
            </button>
          ) : (
            <button type="button" className="btn-outline" disabled={!data || upload.isPending} onClick={() => fileRef.current?.click()} title="upload files into this bin · or paste an image (Ctrl+V)">
              {upload.isPending ? `${uploadDone}/${uploadTotal}…` : "⇪ Upload"}
            </button>
          )}
          {!generating && canClip && selected && (
            <button type="button" className="btn-outline" onClick={() => navigate(`${binPath(binId ?? "")}/clip/${encodeURIComponent(selected.id)}`)} title="cut a clip or lift a still from the selected video">
              ✂ Clip
            </button>
          )}
          {!generating && selected?.type === "image" && !isGif(selected.path) && (
            <button
              type="button"
              className="btn-outline"
              disabled={flip.isPending}
              onClick={() =>
                flip.mutate(
                  { media: selected.id, bin: binId },
                  { onSuccess: () => showToast("flipped copy added"), onError: (err) => showToast(errorText(err)) },
                )
              }
              title="add a horizontally flipped copy of the image on view to this bin — the original stays"
            >
              {flip.isPending ? "flipping…" : "⇋ Flip"}
            </button>
          )}
          {!generating && selected && (
            <button
              type="button"
              className="btn-outline"
              disabled={command.isPending}
              onClick={() => setStartFrom(true)}
              title="reuse the media on view as a reference for a new generation — here, in another bin, or a new one"
            >
              ✦ Reuse
            </button>
          )}
          <span className="spacer" />
          {busy && <JobStatusLine job={jobs[0]} />}
          {generating && data && !generate.isPending && <LaterToggle />}
          {generating && data?.kind === "video" && data.capacity.supports_size && !(!data || generate.isPending) && (
            <QuarterToggle width={project.data?.defaults.width} height={project.data?.defaults.height} />
          )}
          {generating && data?.capacity?.supports_draft && !generate.isPending && <DraftToggle />}
          {accept?.ref && lead && lead !== accept.current && acceptReturn && (
            <>
              <button
                type="button"
                className="btn-outline"
                disabled={command.isPending}
                onClick={() => navigate(acceptReturn, { replace: true })}
                title={`keep @${accept.handle ?? "reference"} as it was and go back`}
              >
                revert
              </button>
              <button
                type="button"
                className="btn-outline is-accent"
                disabled={!data || command.isPending}
                onClick={onUseAsReference}
                title={`make @${accept.handle ?? "reference"} use this take and go back`}
              >
                use as reference
              </button>
            </>
          )}
          {!generating && accept && !accept.ref && (
            <button type="button" className="btn-outline is-accent" disabled={!selected || command.isPending} onClick={onAccept} title="bind the selected media as a reference and go back">
              use as reference
            </button>
          )}
          {generating ? (
            <button
              type="button"
              className="btn-primary"
              disabled={!data || generate.isPending || !data.bin.workflow}
              onClick={onGenerate}
              title={
                data && !data.bin.workflow
                  ? "choose a workflow first"
                  : later
                    ? "snapshot this recipe as it stands; it is generated when you start the waiting group"
                    : busy
                      ? "start another take alongside — each draws its own seed"
                      : "add a take to this bin"
              }
            >
              {generate.isPending ? (
                <>
                  <span className="spinner" aria-hidden="true" />
                  {later ? "saving…" : "starting…"}
                </>
              ) : later ? (
                "Generate later"
              ) : (
                "Generate"
              )}
            </button>
          ) : (
            <button type="button" className="btn-outline btn-open-recipe" disabled={!data} onClick={() => navigate(withMode(true))} title="open the recipe: workflow, prompt, references">
              Generate ▸
            </button>
          )}
          <input ref={fileRef} type="file" accept="image/*,video/*,audio/*" multiple style={{ display: "none" }} onChange={onUploadPicked} />
        </footer>
      </div>
      {pendingDelete && data && (
        <ConfirmSheet
          title={`Delete ${pendingLabel}?`}
          detail="It leaves this bin, every other bin holding it, and every reference using it. A generated file stays on disk. This cannot be undone."
          confirmLabel="Delete media"
          onConfirm={() => onDeleteMedia(pendingDelete)}
          onCancel={() => setPendingDelete(null)}
        />
      )}
      {startFrom && data && slug && selected && (
        <StartFromSheet
          slug={slug}
          bins={project.data?.bins ?? []}
          mediaId={selected.id}
          mediaLabel={mediaLabel(data.bin, selected)}
          defaultBin={binId}
          onDone={(id) => {
            setStartFrom(false);
            navigate(`${binPath(id)}?generate`);
          }}
          onClose={() => setStartFrom(false)}
        />
      )}
      {pastedForRef && slug && binId && (
        <UploadSheet
          slug={slug}
          bins={project.data?.bins ?? []}
          defaultBin={binId}
          initialFiles={pastedForRef}
          onDone={(results) => {
            setPastedForRef(null);
            if (results[0]) onBindReference(results[0].media.id);
          }}
          onClose={() => setPastedForRef(null)}
        />
      )}
      {libPicker.open && libPicker.target === "doc" && slug && binId && (
        <LibraryPicker
          slug={slug}
          title={`Reference for ${data?.bin.name ?? "bin"}`}
          activeBin={binId}
          initialType={libPicker.want}
          onPick={onBindReference}
          onNewBin={() =>
            createBin.create({
              workflow: project.data?.defaults.image_workflow || undefined,
              query: { for: binId, back: binPath(binId) },
            })
          }
          onClose={libPicker.closePicker}
        />
      )}
    </Shell>
  );
}
