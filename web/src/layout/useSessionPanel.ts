import { useCallback } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { canGoBack } from "./history";

export const PANEL_PARAM = "panel";
export const PANEL_VALUE = "session";

/**
 * Below 1120 the session view is a full-screen push, and it lives in the URL as
 * `?panel=session` so the browser's back (and a phone's swipe) closes it. Opening
 * pushes an entry; closing pops when this app pushed it, else strips the param in place
 * so a deep link still has a way out. At 1120 and up the rail ignores the param.
 */
export function useSessionPanel() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const location = useLocation();
  const open = params.get(PANEL_PARAM) === PANEL_VALUE;

  const openPanel = useCallback(() => {
    if (open) return;
    const next = new URLSearchParams(location.search);
    next.set(PANEL_PARAM, PANEL_VALUE);
    navigate({ pathname: location.pathname, search: `?${next}` });
  }, [open, location.pathname, location.search, navigate]);

  const closePanel = useCallback(() => {
    if (!open) return;
    if (canGoBack()) {
      navigate(-1);
      return;
    }
    const next = new URLSearchParams(location.search);
    next.delete(PANEL_PARAM);
    navigate({ pathname: location.pathname, search: next.toString() ? `?${next}` : "" }, { replace: true });
  }, [open, location.pathname, location.search, navigate]);

  return { open, openPanel, closePanel };
}
