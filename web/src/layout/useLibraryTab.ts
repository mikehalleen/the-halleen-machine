import { useEffect, useState } from "react";

export type LibraryTab = "bins" | "sequences";

const KEY = "thm.libraryTab";
const EVENT = "thm-library-tab";

export function stored(slug: string | undefined): LibraryTab | null {
  try {
    const v = slug ? sessionStorage.getItem(`${KEY}.${slug}`) : null;
    return v === "bins" || v === "sequences" ? v : null;
  } catch {
    return null;
  }
}

function remember(slug: string | undefined, t: LibraryTab) {
  try {
    if (slug) sessionStorage.setItem(`${KEY}.${slug}`, t);
  } catch {
    /* storage off: the tab just isn't remembered */
  }
}

/** Bring a project's tab forward from elsewhere (the `Bins` / `Sequences` crumb). */
export function showLibraryTab(slug: string | undefined, t: LibraryTab) {
  remember(slug, t);
  window.dispatchEvent(new CustomEvent(EVENT, { detail: { slug, tab: t } }));
}

/** The crumb between a project and one of its bins or sequences: goes to the project on that tab. */
export function libraryCrumb(slug: string | undefined, t: LibraryTab) {
  return {
    label: t === "bins" ? "Bins" : "Sequences",
    to: `/p/${encodeURIComponent(slug ?? "")}`,
    title: t === "bins" ? "this project's bins" : "this project's sequences",
    onClick: () => showLibraryTab(slug, t),
  };
}

/** Which half of a project's library shows, bins (the default) or sequences. Remembered per
 * project for the tab's life. */
export function useLibraryTab(slug: string | undefined): [LibraryTab, (t: LibraryTab) => void] {
  const [tab, setTab] = useState<LibraryTab>(() => stored(slug) ?? "bins");
  useEffect(() => {
    setTab(stored(slug) ?? "bins");
  }, [slug]);
  useEffect(() => {
    const on = (e: Event) => {
      const d = (e as CustomEvent<{ slug?: string; tab: LibraryTab }>).detail;
      if (d.slug === slug) setTab(d.tab);
    };
    window.addEventListener(EVENT, on);
    return () => window.removeEventListener(EVENT, on);
  }, [slug]);
  const choose = (t: LibraryTab) => {
    setTab(t);
    remember(slug, t);
  };
  return [tab, choose];
}
