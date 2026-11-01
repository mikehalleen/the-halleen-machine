import { useCallback } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { canGoBack } from "./history";

export const VIEW_PARAM = "view";
/** Reference mode: the viewer was opened from a reference tile on bin `vfor`, reference `vref`;
 * it pages the bound media's bin and can swap that reference in place. */
export const VIEW_FOR_PARAM = "vfor";
export const VIEW_REF_PARAM = "vref";

/**
 * The full-screen media preview lives in the URL as `?view=<slug>/<media id>`, so the
 * browser's back (and a phone's swipe) closes it and a link to it can be shared. Opening
 * pushes an entry; closing pops when this app pushed it, else strips the param in place.
 * It stacks on top of the session push, whose own param stays untouched.
 */
export function useMediaViewer() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const location = useLocation();
  const raw = params.get(VIEW_PARAM);
  const slash = raw ? raw.indexOf("/") : -1;
  const slug = raw && slash > 0 ? raw.slice(0, slash) : null;
  const id = raw && slash > 0 ? raw.slice(slash + 1) : null;
  const refFor = raw ? params.get(VIEW_FOR_PARAM) : null;
  const refId = raw ? params.get(VIEW_REF_PARAM) : null;

  /** `replace` swaps the media showing without stacking a history entry: stepping off a media
   * that no longer exists (deleted here) must not leave it behind for the browser's back. A
   * step keeps the reference mode it is in; a fresh open is in it only when `extra` says so. */
  const open = useCallback(
    (mediaSlug: string, mediaId: string, replace = false, extra?: Record<string, string>) => {
      const next = new URLSearchParams(location.search);
      if (!replace) {
        next.delete(VIEW_FOR_PARAM);
        next.delete(VIEW_REF_PARAM);
      }
      for (const [k, v] of Object.entries(extra ?? {})) next.set(k, v);
      next.set(VIEW_PARAM, `${mediaSlug}/${mediaId}`);
      navigate({ pathname: location.pathname, search: `?${next}` }, { replace });
    },
    [location.pathname, location.search, navigate],
  );

  const close = useCallback(() => {
    if (!raw) return;
    if (canGoBack()) {
      navigate(-1);
      return;
    }
    const next = new URLSearchParams(location.search);
    next.delete(VIEW_PARAM);
    next.delete(VIEW_FOR_PARAM);
    next.delete(VIEW_REF_PARAM);
    navigate({ pathname: location.pathname, search: next.toString() ? `?${next}` : "" }, { replace: true });
  }, [raw, location.pathname, location.search, navigate]);

  return { slug, id, refFor, refId, open, close };
}

/**
 * Where "make another like this" goes: back to the bin the take was generated in, which sees
 * `?recall=` and restores the settings that made it. Only a generated media has a recipe.
 */
export function recallPath(slug: string, media: { id: string; origin?: { type?: string | null; bin?: string | null } | null }): string | null {
  const origin = media.origin;
  if (!origin || origin.type !== "generation" || !origin.bin) return null;
  return `/p/${encodeURIComponent(slug)}/b/${encodeURIComponent(origin.bin)}?recall=${encodeURIComponent(media.id)}`;
}
