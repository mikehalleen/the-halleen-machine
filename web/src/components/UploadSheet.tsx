import { useRef, useState } from "react";
import { uploadSummary, useUploadMany } from "../api/client";
import type { BinCard, UploadOut } from "../api/types";
import { useToast } from "./Toast";

interface Props {
  slug: string;
  bins: BinCard[];
  /** The bin the sheet was opened from: preselected, so an upload lands where you are. */
  defaultBin?: string;
  /** Files already chosen -- a drop on the agent panel -- so the sheet only asks which bin. */
  initialFiles?: File[];
  onDone: (results: UploadOut[]) => void;
  onClose: () => void;
}

/** The "upload into which bin?" bottom sheet — shared by the library picker (binds the first
 * result as a reference) and anywhere else that adds to the library. Takes a whole selection:
 * pick five images and they all land in the one bin. With no bin picked and no name typed the
 * server mints the next `bin-n`. */
export function UploadSheet({ slug, bins, defaultBin, initialFiles, onDone, onClose }: Props) {
  const toast = useToast();
  const upload = useUploadMany(slug);
  const fileRef = useRef<HTMLInputElement>(null);
  const [binId, setBinId] = useState(defaultBin && bins.some((b) => b.id === defaultBin) ? defaultBin : "");
  const [newName, setNewName] = useState("");
  const [files, setFiles] = useState<File[]>(initialFiles ?? []);
  const [done, setDone] = useState(0);

  const label = files.length === 0 ? "choose files…" : files.length === 1 ? files[0].name : `${files.length} files`;

  const confirm = () => {
    if (!files.length) {
      toast("choose a file first");
      return;
    }
    setDone(0);
    upload.mutate(
      {
        files,
        bin: binId || undefined,
        binName: !binId && newName.trim() ? newName.trim() : undefined,
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
        <span className="tag">upload into which bin?</span>
        <div className="row wrap chips">
          {bins.map((b) => (
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
          placeholder="or type a new bin name… (blank = the next bin-n)"
          value={newName}
          onChange={(e) => {
            setNewName(e.target.value);
            setBinId("");
          }}
        />
        <label className="field-input row">
          <span className="spacer">{label}</span>
          <input
            ref={fileRef}
            type="file"
            accept="image/*,video/*"
            multiple
            style={{ display: "none" }}
            onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
          />
          <span className="chip">browse</span>
        </label>
        <button type="button" className="btn-primary btn-block" disabled={upload.isPending} onClick={confirm}>
          {upload.isPending ? `uploading ${done} / ${files.length}…` : files.length > 1 ? `Upload ${files.length}` : "Upload"}
        </button>
      </div>
    </div>
  );
}
