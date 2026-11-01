import { useState } from "react";
import type { BinCard } from "../api/types";
import { readBinSort, sortBins } from "../layout/useBinFilter";

interface Props {
  title: string;
  bins: BinCard[];
  /** The bin chosen now, preselected. */
  current?: string | null;
  /** What happens when nothing is picked and no name typed, said under the name field. */
  blankHint: string;
  busy?: boolean;
  /** A bin picked (`bin`), a new one to make by name (`name`), or neither (blank: the default). */
  onPick: (choice: { bin?: string; name?: string }) => void;
  onClose: () => void;
}

/**
 * "Which bin?" as a bottom sheet, the same chooser the upload uses: the bins as chips, or a
 * name for a new one. For choices that are not uploads (where an export lands, say).
 */
export function BinPickSheet({ title, bins, current, blankHint, busy, onPick, onClose }: Props) {
  const preselected = current && bins.some((b) => b.id === current) ? current : "";
  const [binId, setBinId] = useState(preselected);
  const [newName, setNewName] = useState("");
  const confirm = () => onPick(binId ? { bin: binId } : newName.trim() ? { name: newName.trim() } : {});
  // the same order the bin explorer is set to (recent, created, shot)
  const label = (b: BinCard) => `${b.shot != null ? `${b.shot} ` : ""}${b.name}`;
  const sorted = sortBins(bins, readBinSort());
  return (
    <div className="upload-scrim" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="upload-sheet">
        <span className="upload-grabber" />
        <span className="tag">{title}</span>
        <div className="row wrap chips">
          {sorted.map((b) => (
            <button
              key={b.id}
              type="button"
              className={`upload-item-chip ${binId === b.id ? "is-on" : ""}`}
              onClick={() => {
                setBinId(b.id);
                setNewName("");
              }}
            >
              {label(b)}
            </button>
          ))}
        </div>
        <input
          className="field-input"
          placeholder={`or a new bin name, short (${blankHint})`}
          maxLength={40}
          value={newName}
          onChange={(e) => {
            setNewName(e.target.value);
            setBinId("");
          }}
          onKeyDown={(e) => e.key === "Enter" && confirm()}
        />
        <button type="button" className="btn-primary btn-block" disabled={busy} onClick={confirm}>
          {busy ? "saving…" : "Use this bin"}
        </button>
      </div>
    </div>
  );
}
