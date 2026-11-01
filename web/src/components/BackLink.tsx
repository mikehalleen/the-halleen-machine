import type { MouseEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { canGoBack } from "../layout/history";

/**
 * The phone screens' ‹. Pops the history when this app has some, so it agrees with the
 * browser's back and a phone's swipe; falls back to a plain link when the screen was
 * opened from outside.
 */
export function BackLink({ to, label }: { to: string; label: string }) {
  const navigate = useNavigate();
  const onClick = (e: MouseEvent) => {
    if (canGoBack()) {
      e.preventDefault();
      navigate(-1);
    }
  };
  return (
    <Link to={to} className="back" aria-label={label} onClick={onClick}>
      ‹
    </Link>
  );
}
