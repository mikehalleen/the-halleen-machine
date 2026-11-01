import { useState } from "react";
import { errorText, useBins, useCommand, useMediaPages, useProject } from "../api/client";
import type { MediaType, UploadOut } from "../api/types";
import { EditableTitle } from "../components/EditableTitle";
import { Hint } from "../components/Hint";
import { useToast } from "../components/Toast";
import { MediaThumb, ratioOf } from "../components/MediaThumb";
import { UploadSheet } from "../components/UploadSheet";
import { useMediaViewer } from "../layout/useMediaViewer";
import { usePasteFiles } from "../layout/usePasteFiles";

/** What the picker was last narrowed to in this project, so the next slot opens on the same
 * view. Per tab, per project, and only what the user picked -- the slot's own media type is
 * not a choice and is never stored. */
interface Narrowed {
  bin: string;
  tag: string;
}

const ANY: Narrowed = { bin: "all", tag: "all" };
const narrowKey = (slug: string) => `thm.libFilter.${slug}`;

function readNarrowed(slug: string): Narrowed {
  try {
    const raw = sessionStorage.getItem(narrowKey(slug));
    const saved = raw ? (JSON.parse(raw) as Partial<Narrowed>) : null;
    return saved ? { bin: saved.bin ?? "all", tag: saved.tag ?? "all" } : ANY;
  } catch {
    return ANY; /* storage unavailable: start wide */
  }
}

function writeNarrowed(slug: string, n: Narrowed) {
  try {
    if (n.bin === "all" && n.tag === "all") sessionStorage.removeItem(narrowKey(slug));
    else sessionStorage.setItem(narrowKey(slug), JSON.stringify(n));
  } catch {
    /* storage unavailable: the filter still works for this visit */
  }
}

interface Props {
  slug: string;
  title: string;
  /** The bin the picker was opened from: the upload sheet lands files there by default. */
  activeBin?: string;
  /** The slot that asked is an image or a video slot, so only that type can bind: it narrows
   * the library silently rather than as a filter the user could widen to the wrong thing. */
  initialType?: MediaType | null;
  onPick: (mediaId: string, binId?: string) => void;
  /** Make a new bin to render the reference in; the bin screen hands its selected media back. */
  onNewBin: () => void;
  onClose: () => void;
}

/**
 * The library picker: browse the media a slot can take, narrow it by tag or bin, upload, or
 * start a new bin, then tap a media to bind it as a reference. The tap is the whole gesture: a
 * chosen tile is a bound tile, and the picker closes on it. The tile's corner ⤢ opens the
 * full-size viewer instead, for a look before choosing.
 *
 * How it was last narrowed is kept per project for the tab, so binding a character in one bin
 * and then opening the picker in the next lands on the same view until it is changed.
 */
export function LibraryPicker({ slug, title, activeBin, initialType, onPick, onNewBin, onClose }: Props) {
  const [narrowed, setNarrowed] = useState<Narrowed>(() => readNarrowed(slug));
  const narrow = (next: Partial<Narrowed>) => {
    const merged = { ...narrowed, ...next };
    setNarrowed(merged);
    writeNarrowed(slug, merged);
  };
  // the sheet, and the files it opened with: a paste anywhere on the picker opens it with them
  const [uploadOpen, setUploadOpen] = useState(false);
  const [pasted, setPasted] = useState<File[]>([]);
  usePasteFiles((files) => {
    setPasted(files);
    setUploadOpen(true);
  });
  const bins = useBins(slug);
  const project = useProject(slug);
  const command = useCommand(slug);
  const toast = useToast();
  const viewer = useMediaViewer();
  const renameBin = (id: string, name: string) =>
    command.mutate({ name: "set_bin_field", body: { bin: id, field: "name", value: name } }, { onError: (e) => toast(errorText(e)) });
  // tags a bin actually carries, in the project's own order: the chips are these and nothing
  // more, so a chip never leads to an empty grid
  const carried = new Set((bins.data ?? []).flatMap((b) => b.tags ?? []));
  const tagsInUse = (project.data?.tags ?? []).filter((t) => carried.has(t));
  // a remembered narrowing whose bin or tag has since gone falls back to the wide view
  const binId = (bins.data ?? []).some((b) => b.id === narrowed.bin) ? narrowed.bin : "all";
  const tag = carried.has(narrowed.tag) ? narrowed.tag : "all";
  const media = useMediaPages(slug, { bin: binId === "all" ? undefined : binId, type: initialType ?? undefined });
  const fetched = media.data?.pages.flatMap((page) => page.items) ?? [];
  // which bin a media belongs to, the way the server resolves it: where it landed first
  const binOf = (mediaId: string) => {
    const m = fetched.find((x) => x.id === mediaId);
    const origin = m?.origin?.bin;
    if (origin && (bins.data ?? []).some((b) => b.id === origin)) return origin;
    return (bins.data ?? []).find((b) => (b.media ?? []).includes(mediaId))?.id;
  };
  // the tag filter is by the media's bin, so it narrows what the server sent
  const list = tag === "all" ? fetched : fetched.filter((m) => ((bins.data ?? []).find((b) => b.id === binOf(m.id))?.tags ?? []).includes(tag));
  // an empty grid means one of two things, and a remembered filter is the likelier one
  const narrowing = binId !== "all" || tag !== "all";

  // a multi-file drop still binds one reference here: the first of what landed
  const uploaded = (results: UploadOut[]) => {
    setUploadOpen(false);
    setPasted([]);
    if (results[0]) onPick(results[0].media.id, results[0].bin.id);
  };

  return (
    <div className="library-slideover" data-screen-label="Library picker">
      <header className="screen-head">
        <button type="button" className="back" aria-label="Close" onClick={onClose}>
          ✕
        </button>
        <b className="screen-title">{title}</b>
        <span className="spacer" />
      </header>
      <div className="screen-body">
        <div className="row">
          <button type="button" className="btn-outline" onClick={() => setUploadOpen(true)} title="upload files · or paste an image (Ctrl+V)">
            ⇪ Upload
          </button>
          <button type="button" className="btn-outline is-accent" onClick={onNewBin}>
            ✦ New bin
          </button>
        </div>
        <div className="row lib-filters">
          {["all", ...tagsInUse].map((t) => (
            <button key={t} type="button" className={`chip ${tag === t ? "is-on" : ""}`} onClick={() => narrow({ tag: t })}>
              {t}
            </button>
          ))}
          <select className="lib-item-select" value={binId} onChange={(e) => narrow({ bin: e.target.value })} aria-label="Filter by bin">
            <option value="all">all bins</option>
            {(bins.data ?? []).map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </select>
        </div>
        {!media.isLoading && list.length === 0 && (
          <div className="lib-empty">
            {narrowing ? "nothing matches this filter" : "nothing here yet"}
            <div className="lib-empty-sub">
              {narrowing ? "the filter is remembered across bins until you change it" : "upload, or make a new bin and generate — its current take comes back here as the reference"}
            </div>
            {narrowing && (
              <button type="button" className="chip" onClick={() => narrow(ANY)}>
                show everything
              </button>
            )}
          </div>
        )}
        <div className="lib-grid is-media">
          {list.map((m) => {
            const bin = (bins.data ?? []).find((b) => b.id === binOf(m.id));
            return (
              <div key={m.id} className="lib-tile">
                <MediaThumb slug={slug} id={m.id} type={m.type} duration={m.duration_s} size={80} ratio={ratioOf(m)} onClick={() => onPick(m.id, binOf(m.id))} />
                <button type="button" className="lib-tile-view" aria-label="Open full size" title="open full size" onClick={() => viewer.open(slug, m.id)}>
                  ⤢
                </button>
                {bin ? (
                  <EditableTitle className="lib-tile-name" value={bin.name} onSave={(name) => renameBin(bin.id, name)} ariaLabel="Bin name" />
                ) : (
                  <span className="lib-tile-name" />
                )}
              </div>
            );
          })}
        </div>
        {media.hasNextPage && (
          <button type="button" className="btn-outline" disabled={media.isFetchingNextPage} onClick={() => media.fetchNextPage()}>
            {media.isFetchingNextPage ? "loading…" : `Load more (${media.data?.pages[0]?.total ?? 0} in the library)`}
          </button>
        )}
        <Hint>tap a media to use it as the reference · ⤢ views it full size · tap a name to rename its bin · references take their bin's name and file type — @rail-dawn.png in prompts</Hint>
      </div>
      {uploadOpen && (
        <UploadSheet
          slug={slug}
          bins={bins.data ?? []}
          defaultBin={activeBin}
          initialFiles={pasted}
          onDone={uploaded}
          onClose={() => {
            setUploadOpen(false);
            setPasted([]);
          }}
        />
      )}
    </div>
  );
}
