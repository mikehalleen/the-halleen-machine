import type { ReactNode } from "react";

export function Hint({ children, center }: { children: ReactNode; center?: boolean }) {
  return <div className={`hint ${center ? "is-center" : ""}`}>{children}</div>;
}
