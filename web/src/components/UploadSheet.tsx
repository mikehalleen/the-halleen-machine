import { useRef, useState } from "react";
import { errorText, uploadSummary, useKeepFile, useUploadMany } from "../api/client";
import type { BinCard, MessageFile, UploadOut } from "../api/types";
import { readBinSort, sortBins } from "../layout/useBinFilter";
import { usePasteFiles } from "../layout/usePasteFiles";
import { useToast } from "./Toast";

interface Props {
  slug: string;
  bins: BinCard[];
  /** The bin the sheet was opened from: preselected, so an upload lands where you are. */
  defaultBin?: string;
  /** A new bin's name to start with when no bin is preselected (the composer's `uploads`). */
  defaultName?: string;
  /** Files already chosen -- a drop on the agent panel -- so the sheet only asks which bin. */
  initialFiles?: File[];
  /** A note's file, already on the server: the sheet only asks which bin and keeps it there. */
  keep?: MessageFile;
  onDone: (results: UploadOut[]) => void;
  onClose: () => void;
}

/** The "upload into which bin?" bottom sheet — shared by the library picker (binds the first
 * result as a reference) and anywhere else that adds to the library. Takes a whole selection:
 * pick five images and they all land in the one bin. With no bin picked and no name typed the
 * server mints the next `bin-n`. With `keep` there is nothing to choose but the bin: the file
 * is a note's, and confirming makes it project media. */
export function UploadSheet({ slug, bins, defaultBin, defaultName, initialFiles, keep, onDone, onClose }: Props) {
  const toast = useToast();
  const upload = useUploadMany(slug);
  const keepFile = useKeepFile(slug);
  const fileRef = useRef<HTMLInputElement>(null);
  const preselected = defaultBin && bins.some((b) => b.id === defaultBin) ? defaultBin : "";
  const [binId, setBinId] = useState(preselected);
  const [newName, setNewName] = useState(preselected ? "" : (defaultName ?? ""));
  const [files, setFiles] = useState<File[]>(initialFiles ?? []);
  const [done, setDone] = useState(0);
  // an open sheet takes a paste as its files: no browse needed for a screenshot
  usePasteFiles((pasted) => setFiles((prev) => [...prev, ...pasted]), !keep);

  // the same order the bin explorer is set to (recent, created, shot)
  const sortedBins = sortBins(bins, readBinSort());
  const label = files.length === 0 ? "choose files…" : files.length === 1 ? files[0].name : `${files.length} files`;
  const pending = upload.isPending || keepFile.isPending;
  const binName = !binId && newName.trim() ? newName.trim() : undefined;

  const confirm = () => {
    if (keep) {
      keepFile.mutate(
        { file: keep.id, bin: binId || undefined, binName },
        {
          onSuccess: (res) => {
            toast(`added to ${res.bin.name}`);
            onDone([res]);
          },
          onError: (e) => toast(errorText(e)),
        },
      );
      return;
    }
    if (!files.length) {
      toast("choose a file first");
      return;
    }
    setDone(0);
    upload.mutate(
      {
        files,
        bin: binId || undefined,
        binName,
        onProgress: setDone,
      },
      {
        onSuccess: (res) => {
          toast(uploadSummary(res, res.uploaded[0]?.bin.name));
          if (res.uploaded.length) onDone(res.uploaded);
          else onClose();
        },
      },
    );
  };

  return (
    <div className="upload-scrim" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="upload-sheet">
        <span className="upload-grabber" />
        <span className="tag">{keep ? `add ${keep.name || "this file"} to which bin?` : "upload into which bin?"}</span>
        <div className="row wrap chips">
          {sortedBins.map((b) => (
            <button
              key={b.id}
              type="button"
              className={`upload-item-chip ${binId === b.id ? "is-on" : ""}`}
              onClick={() => {
                setBinId(b.id);
                setNewName("");
              }}
            >
              {b.name}
            </button>
          ))}
        </div>
        <input
          className="field-input"
          placeholder="or a new bin name, short (blank = the next bin-n)"
          maxLength={40}
          value={newName}
          onChange={(e) => {
            setNewName(e.target.value);
            setBinId("");
          }}
        />
        {!keep && (
          <label className="field-input row">
            <span className="spacer">{label}</span>
            <input
              ref={fileRef}
              type="file"
              accept="image/*,video/*,audio/*"
              multiple
              style={{ display: "none" }}
              onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
            />
            <span className="chip">browse</span>
          </label>
        )}
        <button type="button" className="btn-primary btn-block" disabled={pending} onClick={confirm}>
          {keep ? (pending ? "adding…" : "Add to bin") : pending ? `uploading ${done} / ${files.length}…` : files.length > 1 ? `Upload ${files.length}` : "Upload"}
        </button>
        {!keep && <div className="hint">or paste an image from the clipboard (Ctrl+V / ⌘V)</div>}
      </div>
    </div>
  );
}
