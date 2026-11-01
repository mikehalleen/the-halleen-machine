import { useCallback } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import type { MediaType } from "../api/types";
import { canGoBack } from "./history";

export const PICKER_PARAM = "picker";
/** The media type a slot asked for, so the picker opens on that filter. */
export const PICKER_TYPE_PARAM = "want";
export type PickerTarget = "doc" | "creator";

/**
 * The library picker: a query-param overlay (`?picker=doc` or `?picker=creator`) on top of
 * whatever screen is showing, per the back-stack rule — it always returns to that screen, so
 * it is state on the current route rather than a route of its own. Same shape as
 * `useSessionPanel`: push on open, pop when this app pushed it, else strip the param in place.
 * `?want=image|video` rides along when an empty slot of that type opened it.
 */
export function useLibraryPicker() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const location = useLocation();
  const raw = params.get(PICKER_PARAM);
  const target: PickerTarget | null = raw === "doc" || raw === "creator" ? raw : null;
  const wantRaw = params.get(PICKER_TYPE_PARAM);
  const want: MediaType | null = wantRaw === "image" || wantRaw === "video" ? wantRaw : null;

  const openPicker = useCallback(
    (t: PickerTarget, type?: MediaType) => {
      const next = new URLSearchParams(location.search);
      next.set(PICKER_PARAM, t);
      if (type) next.set(PICKER_TYPE_PARAM, type);
      else next.delete(PICKER_TYPE_PARAM);
      navigate({ pathname: location.pathname, search: `?${next}` });
    },
    [location.pathname, location.search, navigate],
  );

  const closePicker = useCallback(() => {
    if (!target) return;
    if (canGoBack()) {
      navigate(-1);
      return;
    }
    const next = new URLSearchParams(location.search);
    next.delete(PICKER_PARAM);
    next.delete(PICKER_TYPE_PARAM);
    navigate({ pathname: location.pathname, search: next.toString() ? `?${next}` : "" }, { replace: true });
  }, [target, location.pathname, location.search, navigate]);

  return { target, open: !!target, want, openPicker, closePicker };
}
