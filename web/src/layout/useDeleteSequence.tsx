import { useEffect, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { errorText, useCommand } from "../api/client";
import type { BinCard, SequenceCard } from "../api/types";
import { useToast } from "../components/Toast";

/**
 * Delete a sequence from the X on its card. Two sheets: the first offers to keep the output
 * bin (an ordinary bin from then on) or take it too; the second, for the latter, is the
 * media-loss confirmation the app uses everywhere media goes. No undo. Deleting the sequence on
 * screen lands back on the project.
 */
export function useDeleteSequence(slug: string | undefined, bins: BinCard[] | undefined, activeSequence?: string) {
  const command = useCommand(slug);
  const toast = useToast();
  const navigate = useNavigate();
  const [pending, setPending] = useState<SequenceCard | null>(null);
  const [confirmExports, setConfirmExports] = useState(false);

  const cancel = () => {
    setPending(null);
    setConfirmExports(false);
  };

  const run = (seq: SequenceCard, deleteExports: boolean) => {
    cancel();
    command.mutate(
      { name: "delete_sequence", body: { sequence: seq.id, delete_exports: deleteExports } },
      {
        onSuccess: (r) => {
          toast(r.change?.description ?? `deleted ${seq.name}`);
          if (seq.id === activeSequence && slug) navigate(`/p/${encodeURIComponent(slug)}`, { replace: true });
        },
        onError: (e) => toast(errorText(e)),
      },
    );
  };

  const outBin = pending?.output_bin ? (bins ?? []).find((b) => b.id === pending.output_bin) : undefined;
  const n = outBin?.media?.length ?? 0;
  const exportsWord = `${n} export${n === 1 ? "" : "s"}`;
  const outName = outBin?.name ?? "its exports bin";

  const sheet: ReactNode = pending ? (
    confirmExports ? (
      <Sheet
        title={`Delete ${exportsWord}?`}
        detail={`They leave ${outName}, every other bin holding them, and every reference using them. Generated files stay on disk. This cannot be undone.`}
        onCancel={cancel}
        actions={[{ label: "Delete media", kind: "danger", onClick: () => run(pending, true) }]}
      />
    ) : !outBin ? (
      <Sheet
        title={`Delete sequence ${pending.name}?`}
        detail={`The ${pending.items} bin${pending.items === 1 ? "" : "s"} in it ${pending.items === 1 ? "isn't" : "aren't"} touched. It has no exports bin yet.`}
        onCancel={cancel}
        actions={[{ label: "Delete sequence", kind: "danger", onClick: () => run(pending, false) }]}
      />
    ) : (
      <Sheet
        title={`Delete sequence ${pending.name}?`}
        detail={`The ${pending.items} bin${pending.items === 1 ? "" : "s"} in it ${pending.items === 1 ? "isn't" : "aren't"} touched. ${n ? `Its ${exportsWord} ${n === 1 ? "is" : "are"} media in the bin ${outName}.` : `Its bin ${outName} is empty.`}`}
        onCancel={cancel}
        actions={[
          { label: "Delete sequence, keep exports", kind: "primary", onClick: () => run(pending, false) },
          { label: "Delete sequence and its exports", kind: "danger-outline", onClick: () => (n ? setConfirmExports(true) : run(pending, true)) },
        ]}
      />
    )
  ) : null;

  return { ask: setPending, sheet, busy: command.isPending };
}

interface SheetAction {
  label: string;
  kind: "primary" | "danger" | "danger-outline";
  onClick: () => void;
}

/** The confirm sheet with more than one way forward; same scrim, same Escape, same cancel. */
function Sheet({ title, detail, actions, onCancel }: { title: string; detail: string; actions: SheetAction[]; onCancel: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);
  return (
    <div className="confirm-scrim" role="dialog" aria-label={title} onClick={(e) => e.target === e.currentTarget && onCancel()}>
      <div className="confirm-sheet seq-delete-sheet">
        <span className="upload-grabber" />
        <b className="confirm-title">{title}</b>
        <span className="confirm-detail">{detail}</span>
        <div className="seq-delete-actions">
          {actions.map((a) => (
            <button
              key={a.label}
              type="button"
              className={a.kind === "primary" ? "btn-outline is-accent btn-wide" : a.kind === "danger" ? "btn-danger btn-wide" : "btn-outline is-danger btn-wide"}
              onClick={a.onClick}
            >
              {a.label}
            </button>
          ))}
          <button type="button" className="btn-outline btn-wide" onClick={onCancel}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
