import { useState } from "react";
import type { BinCard } from "../api/types";

/** `all`, `new` (bins holding takes nobody has looked at yet), or one tag from the project's vocabulary. */
export type BinFilter = string;
export const NEW_FILTER = "new";

/** How the list reads: last touched first (an edit by either actor, a take landing), the order
 * the bins were made, or shot number. `shot` is only offered while some bin carries a number. */
export type BinSort = "updated" | "created" | "name" | "shot";
const SORT_KEY = "thm.binSort";
const PICK_KEY = "thm.binFilter";

/** A project's picked chip and typed filter, kept for the browser tab's life. */
function readPick(slug: string | undefined): { tag: BinFilter; query: string } {
  try {
    const v = slug ? JSON.parse(sessionStorage.getItem(`${PICK_KEY}.${slug}`) ?? "null") : null;
    return { tag: typeof v?.tag === "string" ? v.tag : "all", query: typeof v?.query === "string" ? v.query : "" };
  } catch {
    return { tag: "all", query: "" };
  }
}

/** The explorer's current sort, for anything else that lists bins (the "which bin?" sheets)
 * and should read in the same order. */
export function readBinSort(): BinSort {
  return readSort();
}

/** Bins in `sort` order: last touched first (`updated`, newest first), the order they were made,
 * or shot number (unnumbered after, by when made). `shot` falls back to last touched when no bin
 * carries a number, the same as the explorer. */
export function sortBins<T extends { name: string; created?: string | null; updated?: string | null; shot?: number | null }>(bins: T[], sort: BinSort): T[] {
  const effective = sort === "shot" && !bins.some((b) => b.shot != null) ? "updated" : sort;
  if (effective === "name") return [...bins].sort(byName);
  if (effective === "created") return [...bins].sort((a, b) => (a.created ?? "").localeCompare(b.created ?? ""));
  if (effective === "shot") return [...bins].sort((a, b) => (a.shot ?? Infinity) - (b.shot ?? Infinity) || (a.created ?? "").localeCompare(b.created ?? ""));
  return [...bins].sort((a, b) => (b.updated ?? "").localeCompare(a.updated ?? ""));
}

/** A to Z by name, case ignored, numbers in number order ("bin-2" before "bin-10"). */
function byName(a: { name: string }, b: { name: string }): number {
  return a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" });
}

function readSort(): BinSort {
  try {
    const v = localStorage.getItem(SORT_KEY);
    return v === "created" || v === "name" || v === "shot" ? v : "updated";
  } catch {
    return "updated";
  }
}

/** The explorer's filter row: what a bin is for (its tags), and its name. Shared by the wide
 * sidebar and the phone library screen so both read the same list the same way. The chips are
 * only the tags some bin carries, in the vocabulary's order (`tags` is defaults first, then
 * the rest) — `all` is always there. A picked tag whose last bin lost it falls back to `all`
 * rather than leaving an empty list behind a chip that is no longer there. With a `slug`, the
 * chip and the typed filter outlast navigation: remembered per project for the browser tab's life. */
export function useBinFilter(bins: BinCard[] | undefined, tags: string[] | undefined, slug?: string) {
  // the pick belongs to one project: another slug reads its own remembered one
  const [pick, setPick] = useState(() => ({ slug, ...readPick(slug) }));
  const current = pick.slug === slug ? pick : { slug, ...readPick(slug) };
  const remember = (next: { tag: BinFilter; query: string }) => {
    setPick({ slug, ...next });
    if (!slug) return;
    try {
      sessionStorage.setItem(`${PICK_KEY}.${slug}`, JSON.stringify(next));
    } catch {
      // a remembered filter is a convenience
    }
  };
  const picked = current.tag;
  const query = current.query;
  const setTag = (tag: BinFilter) => remember({ tag, query });
  const setQuery = (q: string) => remember({ tag: picked, query: q });
  const [pickedSort, setPickedSort] = useState<BinSort>(readSort);
  const setSort = (s: BinSort) => {
    setPickedSort(s);
    try {
      localStorage.setItem(SORT_KEY, s);
    } catch {
      // a remembered sort is a convenience
    }
  };
  const carried = new Set((bins ?? []).flatMap((b) => b.tags ?? []));
  const inUse = (tags ?? []).filter((t) => carried.has(t));
  const fresh = (bins ?? []).filter((b) => (b.unseen ?? []).length > 0).length;
  const hasShots = (bins ?? []).some((b) => b.shot != null);
  // a project without shot numbers reads by last touched, whatever was picked elsewhere
  const sort: BinSort = pickedSort === "shot" && !hasShots ? "updated" : pickedSort;
  // the blue-marked bins: what landed and still wants looking at. The chip stays while empty, so a
  // review pass can sit on it and watch the list fill as takes land
  const tag = picked === NEW_FILTER ? NEW_FILTER : picked !== "all" && carried.has(picked) ? picked : "all";
  const q = query.trim().toLowerCase();
  const matched = (bins ?? []).filter((b) => {
    if (tag === NEW_FILTER && !(b.unseen ?? []).length) return false;
    if (tag !== "all" && tag !== NEW_FILTER && !(b.tags ?? []).includes(tag)) return false;
    return !q || b.name.toLowerCase().includes(q);
  });
  // the server sends last-touched first; the other orders sort a copy, stable on that
  const shown =
    sort === "created"
      ? [...matched].sort((a, b) => (a.created ?? "").localeCompare(b.created ?? ""))
      : sort === "name"
        ? [...matched].sort(byName)
      : sort === "shot"
        ? [...matched].sort((a, b) => (a.shot ?? Infinity) - (b.shot ?? Infinity) || (a.created ?? "").localeCompare(b.created ?? ""))
        : matched;
  return { tag, setTag, query, setQuery, sort, setSort, hasShots, shown, filtered: tag !== "all" || !!q, inUse, fresh };
}
