import { useEffect } from "react";

interface Props {
  title: string;
  detail?: string;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * The confirm for anything that destroys something.
 *
 * Deliberately not a second tap on the control you just tapped: a bottom sheet puts the
 * destructive button at the far end of the screen from any ✕ in a grid, so a double tap, a
 * repeated tap on a slow connection, or a thumb sliding off a small target lands on the scrim —
 * which cancels — rather than completing the delete. Escape cancels too, and the sheet says what
 * is about to go rather than only asking whether you are sure.
 */
export function ConfirmSheet({ title, detail, confirmLabel, onConfirm, onCancel }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return (
    <div className="confirm-scrim" role="dialog" aria-label={title} onClick={(e) => e.target === e.currentTarget && onCancel()}>
      <div className="confirm-sheet">
        <span className="upload-grabber" />
        <b className="confirm-title">{title}</b>
        {detail && <span className="confirm-detail">{detail}</span>}
        <div className="row confirm-actions">
          <button type="button" className="btn-outline btn-wide" onClick={onCancel}>
            Cancel
          </button>
          <button type="button" className="btn-danger" onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
