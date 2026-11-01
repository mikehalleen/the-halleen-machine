import { useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { canGoBack } from "./history";

/**
 * The ✕ on a nested full-screen route (creator, review, clipper, item), and the return trip
 * after one of them commits.
 *
 * Pops when this app has history to pop, so closing agrees with the browser's back and a
 * phone's swipe. Pushing the screen you came from instead is what makes a single
 * open-and-close cost *two* history entries: five trips into the clipper and back leaves ten
 * entries alternating clipper/shot, and the way out is buried under all of them. Falls back
 * to a replace — never a push — when the screen was opened from outside the app.
 */
export function useCloseTo(path: string) {
  const navigate = useNavigate();
  return useCallback(() => {
    if (canGoBack()) navigate(-1);
    else navigate(path, { replace: true });
  }, [navigate, path]);
}
