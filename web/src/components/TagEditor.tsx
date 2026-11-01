import { useState } from "react";

interface Props {
  /** The bin's tags, in order. */
  tags: string[];
  /** The project's vocabulary, offered as suggestions for the ones not yet on the bin. */
  suggestions: string[];
  onChange: (tags: string[]) => void;
  disabled?: boolean;
}

/** The bin's tags as chips, each removable, and a `+` that opens a small input with the
 * project's other tags as suggestions. Enter or blur commits, and so does picking a suggestion
 * from the list (no second step); Escape cancels. Tags are lowercased and trimmed here the same
 * way the server stores them, so the chip you see is the one that comes back. */
export function TagEditor({ tags, suggestions, onChange, disabled }: Props) {
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState("");
  const listId = "tag-suggestions";
  const offer = suggestions.filter((t) => !tags.includes(t));

  const commit = (value = draft) => {
    const tag = value.trim().toLowerCase();
    setDraft("");
    setAdding(false);
    if (tag && !tags.includes(tag)) onChange([...tags, tag]);
  };

  return (
    <div className="row wrap chips tag-editor">
      {tags.map((t) => (
        <span key={t} className="chip is-on tag-chip">
          {t}
          <button type="button" className="tag-remove" aria-label={`Remove tag ${t}`} title="remove this tag" disabled={disabled} onClick={() => onChange(tags.filter((x) => x !== t))}>
            ✕
          </button>
        </span>
      ))}
      {adding ? (
        <>
          <input
            className="tag-input"
            list={listId}
            value={draft}
            placeholder="tag…"
            aria-label="New tag"
            autoFocus
            onChange={(e) => {
              const v = e.target.value;
              // a pick from the suggestion list arrives as a non-typing input (Chrome reports
              // insertReplacementText; others report nothing) — commit it on the spot
              const how = (e.nativeEvent as InputEvent).inputType;
              const typed = !!how && (how.startsWith("insert") ? how === "insertText" || how === "insertCompositionText" : true);
              if (!typed && offer.includes(v.trim().toLowerCase())) {
                commit(v);
                return;
              }
              setDraft(v);
            }}
            onBlur={() => commit()}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                commit();
              } else if (e.key === "Escape") {
                setDraft("");
                setAdding(false);
              }
            }}
          />
          <datalist id={listId}>
            {offer.map((t) => (
              <option key={t} value={t} />
            ))}
          </datalist>
        </>
      ) : (
        <button type="button" className="chip is-dashed" disabled={disabled} onClick={() => setAdding(true)} title="tag this bin: character, pose, location, clip, keyframe, or your own">
          + tag
        </button>
      )}
    </div>
  );
}
