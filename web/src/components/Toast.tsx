import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

/** Prototype: one toast slot, bottom centre, self-clearing after 2400 ms, above every panel. */
export const TOAST_MS = 2400;

const ToastContext = createContext<(text: string) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const show = useCallback((text: string) => {
    window.clearTimeout(timer.current);
    setToast(text);
    timer.current = window.setTimeout(() => setToast(null), TOAST_MS);
  }, []);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  return (
    <ToastContext.Provider value={show}>
      {children}
      {toast && (
        <div className="toast-fixed" role="status">
          {toast}
        </div>
      )}
    </ToastContext.Provider>
  );
}

export function useToast(): (text: string) => void {
  return useContext(ToastContext);
}
