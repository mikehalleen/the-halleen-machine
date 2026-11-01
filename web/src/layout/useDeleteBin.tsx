import { useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { errorText, useCommand } from "../api/client";
import type { BinCard } from "../api/types";
import { ConfirmSheet } from "../components/ConfirmSheet";
import { useToast } from "../components/Toast";

/**
 * Delete a bin from any list that shows one: the small X on the row asks, the confirm sheet
 * finishes it. Shared by the phone lists and the wide sidebar so both go through the same
 * `delete_bin` command and the same words. Deleting the bin that is open lands on the next
 * surviving bin — chosen here from `bins`, not by going back to the project, whose wide layout
 * would redirect to the first bin in a list that has not refetched yet.
 */
export function useDeleteBin(slug: string | undefined, bins: BinCard[] | undefined, activeBin?: string) {
  const command = useCommand(slug);
  const toast = useToast();
  const navigate = useNavigate();
  const [pending, setPending] = useState<BinCard | null>(null);

  const confirm = (bin: BinCard) => {
    setPending(null);
    command.mutate(
      { name: "delete_bin", body: { bin: bin.id } },
      {
        onSuccess: (r) => {
          toast(r.change?.description ?? `deleted ${bin.name}`);
          if (bin.id === activeBin && slug) {
            const next = (bins ?? []).find((b) => b.id !== bin.id);
            const base = `/p/${encodeURIComponent(slug)}`;
            navigate(next ? `${base}/b/${encodeURIComponent(next.id)}` : base, { replace: true });
          }
        },
        onError: (e) => toast(errorText(e)),
      },
    );
  };

  const n = pending?.media?.length ?? 0;
  const sheet: ReactNode = pending ? (
    <ConfirmSheet
      title={`Delete ${pending.name}?`}
      detail={`${n} media ${n === 1 ? "goes" : "go"} with it, except any held by another bin or bound as a reference. Files on disk stay. This cannot be undone.`}
      confirmLabel="Delete bin"
      onConfirm={() => confirm(pending)}
      onCancel={() => setPending(null)}
    />
  ) : null;

  return { ask: setPending, sheet, busy: command.isPending };
}
