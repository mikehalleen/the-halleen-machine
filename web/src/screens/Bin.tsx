import { useCallback, useEffect, useRef, useState, type ChangeEvent } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { errorText, liveJobsFor, uploadSummary, useBin, useCancelJob, useCommand, useDiscardHeld, useGenerate, useHeld, useLiveJobs, useMarkSeen, useProject, useRecipe, useSetBinTags, useUploadMany, useWorkflows, type LiveBinDetail } from "../api/client";
import { AutoTextarea } from "../components/AutoTextarea";
import { BackLink } from "../components/BackLink";
import { ConfirmSheet } from "../components/ConfirmSheet";
import { EditableTitle } from "../components/EditableTitle";
import { EnhancePrompt } from "../components/EnhancePrompt";
import { LengthField } from "../components/LengthField";
import { QuarterToggle, quarterParams, useQuarterSize } from "../components/SizeField";
import { LATER_GLYPH, LaterToggle, useLater } from "../components/LaterToggle";
import { Hint } from "../components/Hint";
import { JobStatusLine, execTimeLabel, jobPhaseLine, jobStepLabel } from "../components/JobProgress";
import { LoraRow } from "../components/LoraRow";
import { MediaThumb, ratioOf } from "../components/MediaThumb";
import { TagEditor } from "../components/TagEditor";
import { TakePreview } from "../components/TakePreview";
import { Tag } from "../components/Tag";
import { useToast } from "../components/Toast";
import { SortableItem, SortableList } from "../components/Sortable";
import { mediaLabel } from "../components/mediaLabel";
import { requestNotifications } from "../layout/notify";
import { Shell } from "../layout/Shell";
import { useBreakpoint } from "../layout/useBreakpoint";
import { useCreateBin } from "../layout/useCreateBin";
import { useLibraryPicker } from "../layout/useLibraryPicker";
import type { BinDetail, CapacityOut, Generation, JobOut, Lora } from "../api/types";
import { workflowLabel } from "./ProjectForm";
import { LibraryPicker } from "./LibraryPicker";


/** One line for a toast; runner errors can carry a log tail. */
function firstLine(text: string | null | undefined): string {
  return (text ?? "").split("\n").find((l) => l.trim()) ?? "unknown error";
}

export function capacityNote(cap: CapacityOut, addressesByToken: boolean): string {
  const total = cap.image_slots + cap.video_slots;
  if (total <= 0) return "";
  if (!cap.slot_node_ids.length && (cap.supports_start_frame || cap.supports_end_frame) && !addressesByToken) {
    const parts = [];
    if (cap.supports_start_frame) parts.push("1st = start frame");
    if (cap.supports_end_frame) parts.push(`${cap.supports_start_frame ? "2nd" : "1st"} = end frame`);
    return `max ${total} ${total === 1 ? "reference" : "references"} — ${parts.join(", ")}`;
  }
  return `up to ${total} references — hint with .name tokens in the prompt`;
}


/** Where the bin screen was opened from, when it was opened to pick a reference for another bin. */
interface Accept {
  /** The bin that wants the reference. */
  forBin: string;
  /** An existing reference on that bin to swap the media of: a gallery tap rebinds it at once,
   * keeping its handle. Absent means bind a new one from the footer. */
  ref?: string;
  /** The handle of that reference, once the referencing bin has loaded. */
  handle?: string;
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
  /** The shown video started playing: it has really been looked at. */
  onPlayed: (id: string) => void;
  onDeleteMedia: (id: string) => void;
  onRecall: (id: string) => void;
  onCommitField: (field: "prompt" | "negative" | "workflow", value: string) => void;
  onCommitLength: (seconds: number) => void;
  onCommitLoras: (loras: Lora[]) => void;
  onAddReference: () => void;
  onOpenReference: (referenceId: string, binId: string) => void;
  onRemoveReference: (referenceId: string) => void;
  onReorderReferences: (order: string[]) => Promise<unknown>;
  agentLive: boolean;
}

function BinBody({ slug, data, wide, accept, generate, lead, jobs, onCancelJob, held, onDiscardHeld, projectTags, onCommitTags, agentLive, onSelect, onPlayed, onDeleteMedia, onRecall, onCommitField, onCommitLength, onCommitLoras, onAddReference, onOpenReference, onRemoveReference, onReorderReferences }: BinBodyProps) {
  const bin = data.bin;
  const workflows = useWorkflows();
  const images = (workflows.data ?? []).filter((w) => w.kind === "image");
  const videos = (workflows.data ?? []).filter((w) => w.kind === "video");
  const known = (workflows.data ?? []).some((w) => w.name === bin.workflow);
  const [promptDraft, setPromptDraft] = useState(bin.prompt);
  const [negativeDraft, setNegativeDraft] = useState(bin.negative);
  useEffect(() => {
    setPromptDraft(bin.prompt);
    setNegativeDraft(bin.negative);
    // reruns only when the server's value actually changes, not on every background
    // refetch — so a same-value refetch mid-edit does not touch what the user is typing
  }, [bin.prompt, bin.negative]);
  // `media` arrives newest first; the selected one leads, else the newest
  const selected = data.media.find((m) => m.id === (lead ?? data.selected)) ?? data.media[0];
  // the strip scrolls sideways rather than wrapping; keep the selected media in view when it changes
  const stripRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!selected) return;
    stripRef.current?.querySelector(`[data-media="${selected.id}"]`)?.scrollIntoView({ inline: "nearest", block: "nearest" });
  }, [selected?.id]);
  // a render just started takes the strip's left end: bring the strip there, wherever it was,
  // so the placeholder is in view without a hunt. Only a new job does this, not a refetch.
  const jobsKey = jobs.map((j) => j.job).join(",");
  const jobsBefore = useRef(jobsKey);
  useEffect(() => {
    const before = new Set(jobsBefore.current.split(","));
    jobsBefore.current = jobsKey;
    if (jobsKey.split(",").some((j) => j && !before.has(j))) stripRef.current?.scrollTo({ left: 0, behavior: "smooth" });
  }, [jobsKey]);
  /**
   * At the wide layout a long prompt scrolls inside its box instead of pushing References below
   * the fold: the prompt's cap is whatever the scroll box's height leaves after everything else
   * in the right column (label row, negative, references, gaps). Measured, not guessed, so the
   * references card can be one row or three, with or without a negative field; the textarea's
   * own resizer honours the resulting max-height (`--prompt-max`, read in base.css).
   */
  /**
   * The gallery at the wide layout: the picture takes whatever height the scroll box leaves
   * after the strip, the hint and the tags card, so the whole column fits without scrolling
   * and the tags stay in view. Measured the same way as the prompt cap; `.preview` reads the
   * result as `--preview-cap` (its width follows through the aspect ratio).
   */
  const leftRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const col = leftRef.current;
    const box = col?.closest(".screen-body") as HTMLElement | null;
    const preview = col?.querySelector<HTMLElement>(".preview");
    if (!wide || generate || !col || !box || !preview) {
      leftRef.current?.querySelector<HTMLElement>(".preview")?.style.removeProperty("--preview-cap");
      return;
    }
    const fit = () => {
      const pad = parseFloat(getComputedStyle(box).paddingTop) + parseFloat(getComputedStyle(box).paddingBottom);
      const others = col.getBoundingClientRect().height - preview.getBoundingClientRect().height;
      preview.style.setProperty("--preview-cap", `${Math.max(160, Math.floor(box.clientHeight - pad - others))}px`);
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(box);
    col.querySelectorAll(":scope > *:not(.preview)").forEach((c) => ro.observe(c));
    return () => ro.disconnect();
  }, [wide, generate, data.media.length]);
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
  }, [wide, generate, data.references.length, data.workflow?.has_negative]);
  const showRefs = data.capacity.image_slots + data.capacity.video_slots > 0 || data.references.length > 0;
  // references draw in the order they resolve: it names the imageN slots and numbers the handles
  const refsById = new Map(data.references.map((r) => [r.id, r]));
  const note = capacityNote(data.capacity, data.capacity.addresses_by_token);
  const badge = selected ? mediaLabel(bin, selected) : "";
  // takes that landed and nobody has looked at yet: ringed in the strip until shown
  const unseen = new Set(bin.unseen ?? []);
  // a placeholder takes the shape of what is coming: the last take's, else the kind's
  const pendingRatio = Math.min(2, Math.max(0.5, ratioOf(selected) ?? (data.kind === "video" ? 16 / 9 : 1)));
  const picking = accept?.ref ? `.${accept.handle ?? "…"}` : "";
  const usedBy = picking ? ` · ${picking}` : "";

  return (
    <div className={`shot-body ${wide ? "is-wide" : ""} ${generate ? "" : "is-gallery"}`}>
      <div className="shot-left" ref={leftRef}>
        {selected ? (
          <TakePreview slug={slug} media={selected} badge={`${badge}${usedBy}`} onPlayed={() => onPlayed(selected.id)} />
        ) : (
          <div className="preview-empty">{jobs.length ? "generating…" : "nothing here yet — Generate or Upload below"}</div>
        )}
        {selected?.origin?.note && (
          <div className="take-note">
            <span className="take-note-label">from {selected.origin.source ?? "a file"}</span>
            <span className="take-note-text">{selected.origin.note}</span>
          </div>
        )}
        <div className="row">
          <span className="tag">GALLERY ({data.media.length})</span>
          {jobs.length > 0 && <span className="tag is-live">{jobs.length === 1 ? "generating" : `generating ×${jobs.length}`}</span>}
          {held.length > 0 && <span className="tag is-held" title="saved for later — start the group from the bar">{held.length} saved</span>}
          {unseen.size > 0 && <span className="tag is-new">{unseen.size} new</span>}
          <span className="spacer" />
        </div>
        <div className="row thumbs-row takes-strip" ref={stripRef}>
          {jobs
            .slice()
            .reverse()
            .map((j) => (
              <div key={j.job} className="thumb-col is-pending" data-job={j.job}>
                <div
                  className="thumb is-pending"
                  style={{ width: Math.round(64 * pendingRatio), height: 64 }}
                  role="status"
                  aria-label={`generating: ${jobPhaseLine(j) ?? "queued"}`}
                  title={jobPhaseLine(j) ?? "generating…"}
                >
                  <span className="thumb-shimmer" aria-hidden="true" />
                </div>
                <span className="take-num is-pending" aria-hidden="true">
                  {jobStepLabel(j) ?? "…"}
                </span>
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
                  className="thumb is-held"
                  style={{ width: Math.round(64 * pendingRatio), height: 64 }}
                  role="status"
                  aria-label="saved for later"
                  title={`saved for later — ${g.prompt_typed || g.prompt_submitted || "no prompt"}`}
                >
                  {LATER_GLYPH}
                </div>
                <span className="take-num is-held" aria-hidden="true">
                  later
                </span>
                <button
                  type="button"
                  className="take-remove"
                  aria-label="Discard this saved generation"
                  title="discard — it will not render"
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
                selected={m.id === selected?.id}
                fresh={unseen.has(m.id)}
                label={mediaLabel(bin, m)}
                onClick={() => (m.id === selected?.id ? undefined : onSelect(m.id))}
              />
              <span className="take-num" aria-hidden="true">
                {mediaLabel(bin, m).replace("take ", "")}
              </span>
              {m.origin?.type === "generation" && (
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
        {picking && <Hint>tap a take to make it {picking} — same name in the prompt · shown = what {picking} is now · Back returns</Hint>}

        {!generate && (
          <div className="card">
            <div className="tag">TAGS</div>
            <TagEditor tags={bin.tags ?? []} suggestions={projectTags} onChange={onCommitTags} />
          </div>
        )}

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
              className="field-input prompt-text veil-text"
              maxRows={40}
              value={promptDraft}
              onChange={setPromptDraft}
              onBlur={() => promptDraft !== bin.prompt && onCommitField("prompt", promptDraft)}
              placeholder="dot tokens resolve to bound references…"
            />
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
          <div className="card card-strong">
            <div className="row">
              <span>References ({data.references.length})</span>
            </div>
            <div className="row refs-row wrap">
              <SortableList ids={data.references.map((r) => r.id)} onCommit={onReorderReferences} layout="grid">
                {(order) =>
                  order.map((id) => {
                    const r = refsById.get(id);
                    if (!r) return null;
                    const fromBin = r.bin_id;
                    return (
                      <SortableItem key={r.id} id={r.id}>
                        {({ item, handle, isDragging }) => (
                          <div
                            className={`ref-tile ${r.missing ? "is-missing" : ""} ${isDragging ? "is-dragging" : ""}`}
                            title={r.media?.path ?? "missing"}
                            {...item}
                            {...handle}
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
                              dim={!r.active}
                              dashed={r.missing}
                              onClick={fromBin ? () => onOpenReference(r.id, fromBin) : undefined}
                            />
                            <button type="button" className="ref-remove" aria-label="Remove reference" onClick={() => onRemoveReference(r.id)}>
                              ✕
                            </button>
                            <span className="ref-label">.{r.handle}</span>
                            {r.sub_label && <span className="ref-sub">{r.sub_label}</span>}
                            {r.missing && <span className="ref-sub">missing</span>}
                          </div>
                        )}
                      </SortableItem>
                    );
                  })
                }
              </SortableList>
              {data.references.length < data.capacity.image_slots + data.capacity.video_slots && (
                <button type="button" className="tile-add" aria-label="Add reference" onClick={onAddReference}>
                  +
                </button>
              )}
            </div>
            {note && <div className="hint">{note}</div>}
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
  const { wide } = useBreakpoint();
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
   *   `?for=<bin>&ref=<reference>` opened by tapping a reference tile: a gallery tap rebinds
   *     that reference to the tapped media at once (same handle), the hero follows it, and
   *     this bin's own selection is left alone.
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
  const accept: Accept | null = forBin ? { forBin, ref: refId, handle: bound?.handle, back: params.get("back") ?? undefined } : null;
  const acceptReturn = accept ? accept.back ?? binPath(accept.forBin) : null;
  // re-picking a reference: the tapped media leads here until the referencing bin reports it
  const [picked, setPicked] = useState<string | null>(null);
  const lead = refId ? picked ?? bound?.media?.id ?? undefined : undefined;
  const selected = data ? data.media.find((m) => m.id === (lead ?? data.selected)) ?? data.media[0] : undefined;
  const dims = selected?.width && selected?.height ? `${selected.width}×${selected.height}` : "";
  const recipe = useRecipe(slug, recallId);
  const [recalledId, setRecalledId] = useState<string | null>(null); // applied once per take asked for
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

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
  useEffect(() => {
    if (!finished) return;
    const key = `${finished.job}:${finished.status}`;
    if (toasted.current === key) return;
    toasted.current = key;
    if (finished.status === "done") {
      const what = finished.takes.length === 1 ? "take generated" : `${finished.takes.length} takes generated`;
      const took = execTimeLabel(finished.exec_seconds);
      showToast(took ? `${what} in ${took}` : what);
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
    return () => seenIfNew(shownId); // moved on: another selection, or the bin left
  }, [shownId, shownType, seenIfNew]);

  const [quarter] = useQuarterSize();
  const [later] = useLater();
  const onGenerate = () => {
    void requestNotifications(); // first Generate is the user gesture that may ask; a no-op once answered
    generate.mutate(
      { params: data?.kind === "video" ? quarterParams(quarter) : {}, hold: later },
      {
        onSuccess: () => later && showToast("saved for later — start the group from the bar when you're done"),
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

  const onSelect = (id: string) =>
    command.mutate(
      { name: "set_bin_field", body: { bin: binId, field: "selected", value: id } },
      { onError: (err) => showToast(errorText(err)) },
    );

  /** Re-picking a reference: the tap is the whole gesture, and the handle stays. */
  const onPickForReference = (id: string) => {
    if (!accept?.ref || !data) return;
    const was = picked;
    setPicked(id);
    command.mutate(
      { name: "rebind_reference", body: { bin: accept.forBin, reference: accept.ref, media: id } },
      {
        onSuccess: () => showToast(`.${accept.handle ?? "reference"} is now ${mediaLabel(data.bin, data.media.find((m) => m.id === id)!)}`),
        onError: (err) => {
          setPicked(was);
          showToast(errorText(err));
        },
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

  // tapping a reference tile opens the bin it came from, with the accept action armed to swap it
  const onOpenReference = (referenceId: string, fromBin: string) =>
    navigate(`${binPath(fromBin)}?for=${encodeURIComponent(binId ?? "")}&ref=${encodeURIComponent(referenceId)}`);

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
  const [uploadDone, setUploadDone] = useState(0);
  const [uploadTotal, setUploadTotal] = useState(0);
  const fileRef = useRef<HTMLInputElement>(null);
  const onUploadPicked = (e: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    if (!files.length || !binId) return;
    setUploadDone(0);
    setUploadTotal(files.length);
    upload.mutate(
      { files, bin: binId, onProgress: setUploadDone },
      { onSuccess: (res) => showToast(uploadSummary(res, data?.bin.name)), onError: (e) => showToast(errorText(e)) },
    );
  };

  const canClip = selected?.type === "video";
  const backTo = acceptReturn ?? `/p/${encodeURIComponent(slug ?? "")}`;
  const backLabel = accept ? "Back" : "Back to project";
  const pendingLabel = pendingDelete && data ? mediaLabel(data.bin, data.media.find((m) => m.id === pendingDelete)!) : "";

  return (
    <Shell slug={slug} binId={binId}>
      <div className="screen" data-screen-label="Bin">
        <header className="screen-head">
          {(!wide || accept) && <BackLink to={backTo} label={backLabel} />}
          <EditableTitle
            className="screen-title"
            value={data?.bin.name ?? "…"}
            onSave={(name) => onCommitField("name", name)}
            ariaLabel="Bin name"
            disabled={!data}
          />
          <span className="spacer" />
          {dims && <Tag>{dims}</Tag>}
          {data?.kind && <Tag>{data.kind}</Tag>}
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
              onPlayed={seenIfNew}
              onDeleteMedia={setPendingDelete}
              onRecall={(id) => navigate(`${binPath(binId ?? "")}?recall=${encodeURIComponent(id)}`)}
              onCommitField={onCommitField}
              onCommitLength={onCommitLength}
              onCommitLoras={onCommitLoras}
              onAddReference={() => libPicker.openPicker("doc")}
              onOpenReference={onOpenReference}
              onRemoveReference={onRemoveReference}
              onReorderReferences={onReorderReferences}
              agentLive={session?.status === "live"}
            />
          )}
        </div>
        <footer className="screen-foot is-right">
          {generating ? (
            <button type="button" className="btn-outline" onClick={() => navigate(withMode(false))} title="back to this bin's gallery">
              ‹ Gallery
            </button>
          ) : (
            <button type="button" className="btn-outline" disabled={!data || upload.isPending} onClick={() => fileRef.current?.click()} title="upload files into this bin">
              {upload.isPending ? `${uploadDone}/${uploadTotal}…` : "Upload"}
            </button>
          )}
          {!generating && canClip && selected && (
            <button type="button" className="btn-outline" onClick={() => navigate(`${binPath(binId ?? "")}/clip/${encodeURIComponent(selected.id)}`)} title="cut a clip or lift a still from the selected video">
              ✂ Clip
            </button>
          )}
          {busy && <JobStatusLine job={jobs[0]} />}
          {generating && data && !generate.isPending && <LaterToggle />}
          {generating && data?.kind === "video" && !(!data || generate.isPending) && (
            <QuarterToggle width={project.data?.defaults.width} height={project.data?.defaults.height} />
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
                    ? "save this recipe as it stands; it renders when you start the saved group"
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
                "Save for later"
              ) : (
                "Generate"
              )}
            </button>
          ) : (
            <button type="button" className="btn-outline btn-open-recipe" disabled={!data} onClick={() => navigate(withMode(true))} title="open the recipe: workflow, prompt, references">
              Generate ▸
            </button>
          )}
          <input ref={fileRef} type="file" accept="image/*,video/*" multiple style={{ display: "none" }} onChange={onUploadPicked} />
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
      {libPicker.open && libPicker.target === "doc" && slug && binId && (
        <LibraryPicker
          slug={slug}
          title={`Reference for ${data?.bin.name ?? "bin"}`}
          activeBin={binId}
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
