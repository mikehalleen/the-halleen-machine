import { useState } from "react";
import { errorText, useBins, useCommand, useMediaPages, useProject } from "../api/client";
import type { MediaType, UploadOut } from "../api/types";
import { EditableTitle } from "../components/EditableTitle";
import { Hint } from "../components/Hint";
import { useToast } from "../components/Toast";
import { MediaThumb, ratioOf } from "../components/MediaThumb";
import { Tag } from "../components/Tag";
import { UploadSheet } from "../components/UploadSheet";
import { useMediaViewer } from "../layout/useMediaViewer";

type Filter = "all" | MediaType;

interface Props {
  slug: string;
  title: string;
  /** The bin the picker was opened from: the upload sheet lands files there by default. */
  activeBin?: string;
  onPick: (mediaId: string, binId?: string) => void;
  /** Make a new bin to render the reference in; the bin screen hands its selected media back. */
  onNewBin: () => void;
  onClose: () => void;
}

/**
 * The library picker: browse every media, filter by bin or type, upload, or start a new bin,
 * then tap a media to bind it as a reference. The tap is the whole gesture: a chosen tile is a
 * bound tile, and the picker closes on it. The tile's corner ⤢ opens the full-size viewer
 * instead, for a look before choosing.
 */
export function LibraryPicker({ slug, title, activeBin, onPick, onNewBin, onClose }: Props) {
  const [type, setType] = useState<Filter>("all");
  const [binId, setBinId] = useState<string>("all");
  const [tag, setTag] = useState<string>("all");
  const [uploadOpen, setUploadOpen] = useState(false);
  const bins = useBins(slug);
  const project = useProject(slug);
  const command = useCommand(slug);
  const toast = useToast();
  const viewer = useMediaViewer();
  const renameBin = (id: string, name: string) =>
    command.mutate({ name: "set_bin_field", body: { bin: id, field: "name", value: name } }, { onError: (e) => toast(errorText(e)) });
  const media = useMediaPages(slug, { bin: binId === "all" ? undefined : binId, type: type === "all" ? undefined : type });
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

  // a multi-file drop still binds one reference here: the first of what landed
  const uploaded = (results: UploadOut[]) => {
    setUploadOpen(false);
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
        <Tag>LIBRARY</Tag>
      </header>
      <div className="screen-body">
        <div className="row">
          <button type="button" className="btn-outline" onClick={() => setUploadOpen(true)}>
            ⇪ Upload
          </button>
          <button type="button" className="btn-outline is-accent" onClick={onNewBin}>
            ✦ New bin
          </button>
        </div>
        <div className="row lib-filters">
          {(["all", "image", "video"] as Filter[]).map((f) => (
            <button key={f} type="button" className={`chip ${type === f ? "is-on" : ""}`} onClick={() => setType(f)}>
              {f}
            </button>
          ))}
          <select className="lib-item-select" value={tag} onChange={(e) => setTag(e.target.value)} aria-label="Filter by tag">
            <option value="all">any tag</option>
            {(project.data?.tags ?? []).map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
          <select className="lib-item-select" value={binId} onChange={(e) => setBinId(e.target.value)} aria-label="Filter by bin">
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
            nothing here yet
            <div className="lib-empty-sub">upload, or make a new bin and generate — its selected media comes back here as the reference</div>
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
        <Hint>tap a media to use it as the reference · ⤢ views it full size · tap a name to rename its bin · references take their bin's name — .rail-dawn in prompts</Hint>
      </div>
      {uploadOpen && <UploadSheet slug={slug} bins={bins.data ?? []} defaultBin={activeBin} onDone={uploaded} onClose={() => setUploadOpen(false)} />}
    </div>
  );
}
