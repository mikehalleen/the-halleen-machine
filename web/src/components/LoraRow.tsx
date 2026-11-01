import { useState } from "react";
import { useLoras } from "../api/client";
import type { Lora } from "../api/types";

interface Props {
  loras: Lora[];
  /** Called with the whole new list after an add, edit, or remove. */
  onChange: (loras: Lora[]) => void;
  /** Whether the workflow has a LoRA loader to feed; chips still show without one, with a warning. */
  supported: boolean;
  disabled?: boolean;
}

/** The chip label: file name without folder or extension. */
export function loraLabel(name: string): string {
  const base = name.split("/").pop() ?? name;
  return base.replace(/\.(safetensors|pt|ckpt)$/i, "");
}

/** What the runner will receive for this entry, for the chip's tooltip. */
export function loraTag(l: Lora): string {
  return l.strength_b == null ? `<lora:${l.name}:${l.strength}>` : `<lora:${l.name}:${l.strength}:${l.strength_b}>`;
}

/**
 * The LoRA chip row: one chip per entry on the bin, tap to edit strength
 * or remove, a dashed chip to add one from the picker. The prompt text never shows them — the
 * assembler puts the `<lora:...>` tags in front of the submitted prompt at generate time.
 */
export function LoraRow({ loras, onChange, supported, disabled }: Props) {
  const [editing, setEditing] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);
  const open = editing ? loras.find((l) => l.name === editing) : undefined;

  const update = (name: string, patch: Partial<Lora>) => onChange(loras.map((l) => (l.name === name ? { ...l, ...patch } : l)));
  const remove = (name: string) => {
    setEditing(null);
    onChange(loras.filter((l) => l.name !== name));
  };
  const add = (name: string) => {
    setPicking(false);
    if (!loras.some((l) => l.name === name)) onChange([...loras, { name, strength: 1 }]);
  };

  return (
    <div className="card">
      <div className="row">
        <span className="tag">LORAS</span>
        <span className="spacer" />
        {!supported && loras.length > 0 && <span className="hint">this workflow has no LoRA loader — these are dropped at generation</span>}
      </div>
      <div className="row chips">
        {loras.map((l) => (
          <button
            key={l.name}
            type="button"
            className={`chip lora-chip ${editing === l.name ? "is-on" : ""}`}
            title={loraTag(l)}
            disabled={disabled}
            onClick={() => setEditing(editing === l.name ? null : l.name)}
          >
            {loraLabel(l.name)} · {l.strength}
            {l.strength_b != null ? `:${l.strength_b}` : ""}
          </button>
        ))}
        <button type="button" className="chip is-dashed" disabled={disabled} onClick={() => setPicking(true)}>
          + lora
        </button>
      </div>
      {open && (
        <div className="row lora-edit">
          <span className="lora-edit-name" title={open.name}>
            {open.name}
          </span>
          <StrengthInput value={open.strength} label="Strength" onChange={(v) => v !== undefined && update(open.name, { strength: v })} />
          <StrengthInput
            value={open.strength_b ?? undefined}
            placeholder="2nd"
            label="Second strength"
            optional
            onChange={(v) => update(open.name, { strength_b: v ?? null })}
          />
          <button type="button" className="link" onClick={() => remove(open.name)}>
            remove
          </button>
        </div>
      )}
      {picking && <LoraSheet taken={loras.map((l) => l.name)} onPick={add} onClose={() => setPicking(false)} />}
    </div>
  );
}

function StrengthInput({
  value,
  onChange,
  label,
  placeholder,
  optional,
}: {
  value: number | undefined;
  onChange: (v: number | undefined) => void;
  label: string;
  placeholder?: string;
  optional?: boolean;
}) {
  // typed as text so a half-typed "0." or an emptied optional field does not snap back mid-edit
  const [draft, setDraft] = useState(value === undefined ? "" : String(value));
  const [last, setLast] = useState(value);
  if (last !== value) {
    setLast(value);
    setDraft(value === undefined ? "" : String(value));
  }
  const settle = () => {
    if (draft.trim() === "" && optional) {
      if (value !== undefined) onChange(undefined);
      return;
    }
    const n = parseFloat(draft);
    if (Number.isNaN(n)) setDraft(value === undefined ? "" : String(value));
    else if (n !== value) onChange(n);
  };
  return (
    <input
      className="field-input lora-strength"
      inputMode="decimal"
      aria-label={label}
      placeholder={placeholder}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={settle}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
    />
  );
}

/** The picker: every file under [paths] loras, filtered as you type; tap adds it at strength 1. */
function LoraSheet({ taken, onPick, onClose }: { taken: string[]; onPick: (name: string) => void; onClose: () => void }) {
  const loras = useLoras();
  const [q, setQ] = useState("");
  const needle = q.trim().toLowerCase();
  const all = loras.data ?? [];
  const shown = needle ? all.filter((l) => l.name.toLowerCase().includes(needle)) : all;
  return (
    <div className="upload-scrim" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="upload-sheet lora-sheet">
        <span className="upload-grabber" />
        <span className="tag">add a lora</span>
        <input className="field-input" placeholder="filter…" value={q} onChange={(e) => setQ(e.target.value)} autoFocus />
        <div className="lora-list">
          {loras.isPending && <div className="hint">loading…</div>}
          {loras.isError && <div className="hint">could not list loras</div>}
          {loras.isSuccess && all.length === 0 && <div className="hint">nothing under [paths] loras in config.toml</div>}
          {loras.isSuccess && all.length > 0 && shown.length === 0 && <div className="hint">no match</div>}
          {shown.map((l) => {
            const has = taken.includes(l.name);
            return (
              <button key={l.name} type="button" className={`lora-option ${has ? "is-taken" : ""}`} disabled={has} onClick={() => onPick(l.name)}>
                <span className="lora-option-name">{l.name}</span>
                <span className="meta">{has ? "added" : `${Math.max(1, Math.round(l.size / (1 << 20)))} MB`}</span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
