import { useCallback, useEffect, useRef, useState, type DragEvent, type ReactNode } from "react";
import { errorText, removeChatFile, uploadChatFile } from "../api/client";
import type { MessageFile } from "../api/types";
import { usePasteFiles } from "../layout/usePasteFiles";
import { FileTile, MessageFiles } from "./MessageFiles";
import { useToast } from "./Toast";
import { useDraft } from "./useDraft";

function dragHasFiles(e: DragEvent): boolean {
  return Array.from(e.dataTransfer?.types ?? []).includes("Files");
}

/**
 * The files on a card's reply: pasted into its note, dropped on it, or picked with `+`. Each
 * uploads at once as a chat file (in no bin, like the composer's) and rides the reply; they
 * survive navigation with the note's draft. A paste counts only when the card's note has the
 * focus, and a drop only on the note area, so the panel's composer keeps everything else.
 */
export function useNoteFiles(slug: string, key: string, enabled: boolean) {
  const [files, setFiles] = useDraft<MessageFile[]>(`${key}.files`, []);
  const [uploading, setUploading] = useState(0);
  const toast = useToast();
  const areaRef = useRef<HTMLDivElement>(null);
  const add = useCallback(
    (picked: File[]) => {
      for (const file of picked) {
        setUploading((n) => n + 1);
        uploadChatFile(slug, file)
          .then((f) => setFiles((prev) => [...prev, f]))
          .catch((err) => toast(`${file.name}: ${errorText(err)}`))
          .finally(() => setUploading((n) => n - 1));
      }
    },
    [slug, setFiles, toast],
  );
  const remove = (f: MessageFile) => {
    setFiles((prev) => prev.filter((p) => p.id !== f.id));
    void removeChatFile(slug, f.id);
  };
  // the files are the reply's once the card is answered: let go of them then, never before, so a
  // send the server refused keeps them on the card
  const clearRef = useRef(() => setFiles([]));
  clearRef.current = () => setFiles([]);
  useEffect(() => {
    if (!enabled) clearRef.current();
  }, [enabled]);
  usePasteFiles((picked, e) => (areaRef.current?.contains(e.target as Node) ? void add(picked) : false), enabled);
  const onDragOver = (e: DragEvent) => {
    if (!dragHasFiles(e)) return;
    e.preventDefault();
    e.stopPropagation(); // the card's, not the composer's
  };
  const onDrop = (e: DragEvent) => {
    if (!dragHasFiles(e)) return;
    e.preventDefault();
    e.stopPropagation();
    add(Array.from(e.dataTransfer.files));
  };
  return {
    files,
    ids: files.map((f) => f.id),
    uploading,
    any: files.length > 0,
    add,
    remove,
    areaRef,
    dropProps: { onDragOver, onDrop },
  };
}

export type NoteFilesState = ReturnType<typeof useNoteFiles>;

/** The note area: chips above the textarea, `+` beside it; drops land here. */
export function NoteFilesArea({ slug, note, children }: { slug: string; note: NoteFilesState; children: ReactNode }) {
  const pickRef = useRef<HTMLInputElement>(null);
  return (
    <div ref={note.areaRef} className="change-note-area" {...note.dropProps}>
      {(note.any || note.uploading > 0) && (
        <div className="composer-chips" aria-label="On this reply">
          {note.files.map((f) => (
            <span key={f.id} className="composer-chip">
              <FileTile slug={slug} file={f} size={40} />
              <button type="button" className="composer-chip-remove" aria-label="Take off this reply" title="take off this reply" onClick={() => note.remove(f)}>
                ✕
              </button>
            </span>
          ))}
          {note.uploading > 0 && <span className="composer-chip-busy" aria-label="Uploading" title="uploading…" />}
        </div>
      )}
      <div className="row change-note-row">
        <button type="button" className="composer-attach change-note-attach" onClick={() => pickRef.current?.click()} title="attach a picture or clip to this reply (or paste / drop one)" aria-label="Attach a file to this reply">
          +
        </button>
        <input
          ref={pickRef}
          type="file"
          accept="image/*,video/*,audio/*"
          multiple
          style={{ display: "none" }}
          onChange={(e) => {
            note.add(Array.from(e.target.files ?? []));
            e.target.value = "";
          }}
        />
        {children}
      </div>
    </div>
  );
}

/** The files a sent reply carried, under its note. */
export function SentFiles({ slug, files }: { slug: string; files?: MessageFile[] | null }) {
  if (!files?.length) return null;
  return <MessageFiles slug={slug} files={files} />;
}
