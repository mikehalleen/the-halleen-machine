import { useState } from "react";
import { readBinSort, sortBins } from "../layout/useBinFilter";
import { errorText, useCommand } from "../api/client";
import type { BinCard } from "../api/types";
import { useToast } from "./Toast";

interface Props {
  slug: string;
  bins: BinCard[];
  /** The media the generation starts from, and what to call it in the sheet (`take 3`, `#2`, a file name). */
  mediaId: string;
  mediaLabel: string;
  /** The bin the sheet was opened from: preselected, so the default is "here". */
  defaultBin?: string;
  /** The bin the reference went into, minted or not: the caller lands on its recipe. */
  onDone: (binId: string) => void;
  onClose: () => void;
}

const NEW = "";

/**
 * The reference-first path's one question: "generate from this, in which bin?" A bottom sheet
 * over the bin screen or the viewer, dismissed by tapping the scrim. The bin you are in is
 * preselected; any other bin is a chip; `✦ new bin` mints one, named or the next `bin-n`.
 * One command does the whole thing (`start_from_media`), and it never refuses on capacity:
 * a reference the workflow has no slot for shows as unused on the recipe.
 */
export function StartFromSheet({ slug, bins, mediaId, mediaLabel, defaultBin, onDone, onClose }: Props) {
  const toast = useToast();
  const command = useCommand(slug);
  const [binId, setBinId] = useState(defaultBin && bins.some((b) => b.id === defaultBin) ? defaultBin : NEW);
  // the same order the bin explorer is set to (recent, created, shot); "new bin" stays first
  const sortedBins = sortBins(bins, readBinSort());
  const [newName, setNewName] = useState("");

  const confirm = () => {
    const body: Record<string, unknown> = { media: mediaId };
    if (binId) body.bin = binId;
    else if (newName.trim()) body.bin_name = newName.trim();
    command.mutate(
      { name: "start_from_media", body },
      {
        onSuccess: (out) => {
          toast(out.change?.description ?? "bound as a reference");
          const went = out.bin ?? binId;
          if (went) onDone(went);
          else onClose();
        },
        onError: (e) => toast(errorText(e)),
      },
    );
  };

  return (
    <div className="upload-scrim" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="upload-sheet" role="dialog" aria-label="Generate from this media">
        <span className="upload-grabber" />
        <span className="tag">generate from {mediaLabel} in which bin?</span>
        <div className="row wrap chips">
          <button
            type="button"
            className={`upload-item-chip ${binId === NEW ? "is-on" : ""}`}
            onClick={() => setBinId(NEW)}
            title="a new bin, with this as its first reference"
          >
            ✦ new bin
          </button>
          {sortedBins.map((b) => (
            <button
              key={b.id}
              type="button"
              className={`upload-item-chip ${binId === b.id ? "is-on" : ""}`}
              onClick={() => setBinId(b.id)}
              title={b.id === defaultBin ? "this bin — the reference joins its recipe" : `bind it into ${b.name}`}
            >
              {b.name}
              {b.id === defaultBin ? " · here" : ""}
            </button>
          ))}
        </div>
        {binId === NEW && (
          <input
            className="field-input"
            placeholder="name the new bin… (blank = the next bin-n)"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            aria-label="New bin name"
          />
        )}
        <button type="button" className="btn-primary btn-block" disabled={command.isPending} onClick={confirm}>
          {command.isPending ? "binding…" : "Generate ▸"}
        </button>
      </div>
    </div>
  );
}
