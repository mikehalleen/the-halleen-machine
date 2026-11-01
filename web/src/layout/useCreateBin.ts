import { useNavigate } from "react-router-dom";
import { errorText, fetchBins, useCommand } from "../api/client";
import type { BinCard } from "../api/types";
import { useToast } from "../components/Toast";

/** `bin-1`, `bin-2`, … — the lowest number nothing is using, so a deleted bin's name comes
 * back around instead of the counter drifting upward. The server does the same when no name
 * is given; the client picks it so it can find the record by name after the create. */
export function nextBinName(bins: BinCard[]): string {
  const taken = new Set(bins.map((b) => b.name.trim().toLowerCase()));
  for (let n = 1; ; n++) {
    const name = `bin-${n}`;
    if (!taken.has(name)) return name;
  }
}

export interface CreateBinOptions {
  /** The workflow to start on; omitted, the server uses the project's image default. */
  workflow?: string;
  /** Query string for the new bin's URL: the accept action's `for`/`back` when the bin is being
   * made as a reference for another bin. */
  query?: Record<string, string>;
}

/**
 * The explorer's and the picker's `＋ New bin`: `create_bin` targets the project, so its response
 * carries no bin id — the client names the bin and finds it by that name afterwards.
 */
export function useCreateBin(slug: string | undefined, bins: BinCard[] | undefined) {
  const command = useCommand(slug);
  const navigate = useNavigate();
  const toast = useToast();

  const create = (opts: CreateBinOptions = {}) => {
    if (!slug) return;
    const name = nextBinName(bins ?? []);
    const body: Record<string, unknown> = { bin_name: name };
    if (opts.workflow) body.workflow = opts.workflow;
    command.mutate(
      { name: "create_bin", body },
      {
        onSuccess: async () => {
          try {
            const list = await fetchBins(slug);
            const made = list.find((b) => b.name === name);
            if (!made) return;
            const qs = new URLSearchParams(opts.query ?? {}).toString();
            navigate(`/p/${encodeURIComponent(slug)}/b/${encodeURIComponent(made.id)}${qs ? `?${qs}` : ""}`);
          } catch (e) {
            toast(errorText(e));
          }
        },
        onError: (e) => toast(errorText(e)),
      },
    );
  };

  return { create, pending: command.isPending };
}
