import { useNavigate } from "react-router-dom";
import { errorText, fetchBins, useCommand } from "../api/client";
import { useToast } from "../components/Toast";

/**
 * The bin screen's `⧉ clone`: `duplicate_bin` makes a new bin with the same recipe — workflow,
 * prompt, negative, loras, tags, description and every reference binding — and no media. The
 * server names it `<name>-copy` and answers with the new id, so the clone opens on its recipe
 * view at once; its gallery is empty and the point is to change it.
 */
export function useCloneBin(slug: string | undefined) {
  const command = useCommand(slug);
  const navigate = useNavigate();
  const toast = useToast();

  const clone = (bin: { id: string; name: string }) => {
    if (!slug) return;
    command.mutate(
      { name: "duplicate_bin", body: { bin: bin.id } },
      {
        onSuccess: async (r) => {
          toast(r.change?.description ?? `duplicated ${bin.name}`);
          let made = r.bin ?? null;
          if (!made) {
            // older server: find the copy by the name the server gives it
            try {
              const list = await fetchBins(slug);
              made = list.find((b) => b.name.toLowerCase().startsWith(`${bin.name}-copy`.toLowerCase()))?.id ?? null;
            } catch (e) {
              toast(errorText(e));
            }
          }
          if (made) navigate(`/p/${encodeURIComponent(slug)}/b/${encodeURIComponent(made)}?generate`);
        },
        onError: (e) => toast(errorText(e)),
      },
    );
  };

  return { clone, pending: command.isPending };
}
