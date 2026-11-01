import { useState } from "react";
import type { BinCard } from "../api/types";

/** `all`, or one tag from the project's vocabulary. */
export type BinFilter = string;

/** The explorer's filter row: what a bin is for (its tags), and its name. Shared by the wide
 * sidebar and the phone library screen so both read the same list the same way. The chips are
 * only the tags some bin carries, in the vocabulary's order (`tags` is defaults first, then
 * the rest) — `all` is always there. A picked tag whose last bin lost it falls back to `all`
 * rather than leaving an empty list behind a chip that is no longer there. */
export function useBinFilter(bins: BinCard[] | undefined, tags: string[] | undefined) {
  const [picked, setTag] = useState<BinFilter>("all");
  const [query, setQuery] = useState("");
  const carried = new Set((bins ?? []).flatMap((b) => b.tags ?? []));
  const inUse = (tags ?? []).filter((t) => carried.has(t));
  const tag = picked !== "all" && carried.has(picked) ? picked : "all";
  const q = query.trim().toLowerCase();
  const shown = (bins ?? []).filter((b) => {
    if (tag !== "all" && !(b.tags ?? []).includes(tag)) return false;
    return !q || b.name.toLowerCase().includes(q);
  });
  return { tag, setTag, query, setQuery, shown, filtered: tag !== "all" || !!q, inUse };
}
