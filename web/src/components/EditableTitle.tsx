import { useEffect, useRef, useState, type KeyboardEvent } from "react";

interface Props {
  value: string;
  /** Called with the trimmed new name when it differs from the last saved one. */
  onSave: (next: string) => void;
  ariaLabel: string;
  className?: string;
  /** Read-only: shows the name and nothing else. */
  disabled?: boolean;
}

/**
 * A name that edits in place: tap it and it becomes a field with the text selected; it saves
 * when you leave — on blur, on Enter, and on unmount for the case where navigation takes the
 * field away without ever blurring it. Escape puts the old name back. Two things keep it from
 * flickering: the seed is keyed on the server's *value*, so a background refetch that returns
 * the same name never touches what you are typing; and `savedRef` is what a commit is diffed
 * against, moved forward optimistically, so the same edit cannot fire twice while the echo is
 * in flight. An empty name is never saved.
 */
export function EditableTitle({ value, onSave, ariaLabel, className = "", disabled }: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const savedRef = useRef(value);
  const draftRef = useRef(value);
  const editingRef = useRef(false);
  const inputRef = useRef<HTMLInputElement>(null);
  draftRef.current = draft;
  editingRef.current = editing;

  useEffect(() => {
    setDraft(value);
    savedRef.current = value;
  }, [value]);

  useEffect(() => {
    if (editing) inputRef.current?.select();
  }, [editing]);

  const commit = () => {
    const next = draftRef.current.trim();
    if (next && next !== savedRef.current) {
      savedRef.current = next;
      onSave(next);
    } else {
      setDraft(savedRef.current);
    }
  };

  // latest closure, called once on unmount — a plain cleanup would re-run on every render
  const commitRef = useRef(commit);
  commitRef.current = commit;
  useEffect(() => () => {
    if (editingRef.current) commitRef.current();
  }, []);

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") e.currentTarget.blur();
    if (e.key === "Escape") {
      setDraft(savedRef.current);
      setEditing(false);
    }
  };

  if (disabled) return <b className={className}>{value}</b>;
  if (!editing) {
    return (
      <b
        className={`editable-title ${className}`}
        role="button"
        tabIndex={0}
        title="tap to rename"
        aria-label={`${ariaLabel}: ${value} (tap to rename)`}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setEditing(true);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setEditing(true);
          }
        }}
      >
        {value}
      </b>
    );
  }
  return (
    <input
      ref={inputRef}
      className={`editable-title-input ${className}`}
      value={draft}
      autoFocus
      aria-label={ariaLabel}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        commit();
        setEditing(false);
      }}
      onKeyDown={onKey}
      onClick={(e) => e.stopPropagation()}
    />
  );
}
