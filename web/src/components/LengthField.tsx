import { useEffect, useState } from "react";

const PRESETS = [3, 5, 8, 10];

/** "4.5", never "4.50" or "4.5000000001". */
function fmt(n: number | null | undefined): string {
  return n == null ? "" : String(Math.round(n * 100) / 100);
}

interface Props {
  value: number | null | undefined;
  onChange: (seconds: number) => void;
}

/**
 * A video length in seconds: the usual presets as chips, and a field for anything else, 4.5
 * included. The field commits on blur and on Enter, rounded to a hundredth; a value that is
 * not a number or not positive is dropped and the last saved length comes back. The runner
 * turns seconds into frames at the workflow's fps, so any length is a legal length.
 */
export function LengthField({ value, onChange }: Props) {
  const [draft, setDraft] = useState(fmt(value));
  useEffect(() => {
    setDraft(fmt(value));
  }, [value]);

  const commit = () => {
    const n = Math.round(parseFloat(draft.replace(",", ".")) * 100) / 100;
    if (!Number.isFinite(n) || n <= 0) {
      setDraft(fmt(value));
      return;
    }
    if (n !== value) onChange(n);
    else setDraft(fmt(n));
  };

  const custom = value != null && !PRESETS.includes(value);
  return (
    <div className="row chips">
      {PRESETS.map((n) => (
        <button key={n} type="button" className={`chip ${value === n ? "is-on" : ""}`} onClick={() => onChange(n)}>
          {n}s
        </button>
      ))}
      <label className={`chip len-chip ${custom ? "is-on" : ""}`}>
        <input
          className="len-input"
          type="text"
          inputMode="decimal"
          value={draft}
          placeholder="…"
          aria-label="Length in seconds"
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") e.currentTarget.blur();
            if (e.key === "Escape") {
              setDraft(fmt(value));
              e.currentTarget.blur();
            }
          }}
        />
        s
      </label>
    </div>
  );
}
