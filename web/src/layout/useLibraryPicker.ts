import { useCallback } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { canGoBack } from "./history";

export const PICKER_PARAM = "picker";
export type PickerTarget = "doc" | "creator";

/**
 * The library picker: a query-param overlay (`?picker=doc` or `?picker=creator`) on top of
 * whatever screen is showing, per the back-stack rule — it always returns to that screen, so
 * it is state on the current route rather than a route of its own. Same shape as
 * `useSessionPanel`: push on open, pop when this app pushed it, else strip the param in place.
 */
export function useLibraryPicker() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const location = useLocation();
  const raw = params.get(PICKER_PARAM);
  const target: PickerTarget | null = raw === "doc" || raw === "creator" ? raw : null;

  const openPicker = useCallback(
    (t: PickerTarget) => {
      const next = new URLSearchParams(location.search);
      next.set(PICKER_PARAM, t);
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
    navigate({ pathname: location.pathname, search: next.toString() ? `?${next}` : "" }, { replace: true });
  }, [target, location.pathname, location.search, navigate]);

  return { target, open: !!target, openPicker, closePicker };
}
