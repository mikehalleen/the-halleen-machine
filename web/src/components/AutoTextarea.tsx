import { useLayoutEffect, useRef, type ChangeEvent, type KeyboardEvent, type RefObject, type TextareaHTMLAttributes } from "react";

interface Props extends Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "onChange" | "value"> {
  value: string;
  onChange: (value: string) => void;
  /** Lines shown when empty; the box grows with its content. */
  minRows?: number;
  maxRows?: number;
  /** Enter submits through this; Shift+Enter or Ctrl+Enter inserts a line break instead. */
  onSubmitKey?: () => void;
  /** The caller's handle on the element, for callers that write at the caret (the `@` menu). */
  inputRef?: RefObject<HTMLTextAreaElement>;
}

/**
 * Every text entry in the app: multi-line, two lines to start, grows as you type. By default
 * Return is a line break and the button beside it submits. Entries given `onSubmitKey` (the
 * agent notes, the prompts, the enhance note) submit on Return and take Shift+Return or
 * Ctrl+Return as the line break.
 */
export function AutoTextarea({ value, onChange, minRows = 2, maxRows = 12, className = "", onSubmitKey, onKeyDown, inputRef, ...rest }: Props) {
  const own = useRef<HTMLTextAreaElement>(null);
  const ref = inputRef ?? own;
  const fit = () => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    const line = parseFloat(getComputedStyle(el).lineHeight) || 18;
    const pad = el.offsetHeight - el.clientHeight;
    const min = line * minRows + pad;
    // a stylesheet max-height (e.g. the shot prompt capped to the viewport) tightens the row cap
    const cssMax = parseFloat(getComputedStyle(el).maxHeight);
    const max = Math.min(line * maxRows + pad, Number.isFinite(cssMax) && cssMax > 0 ? cssMax : Infinity);
    const next = Math.min(Math.max(el.scrollHeight, min), max);
    el.style.height = `${next}px`;
    el.style.overflowY = el.scrollHeight > max ? "auto" : "hidden";
  };
  useLayoutEffect(fit, [value, minRows, maxRows]);
  // the cap can move under the box (a stylesheet max-height that follows the viewport): refit then too
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    window.addEventListener("resize", fit);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", fit);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <textarea
      ref={ref}
      rows={minRows}
      className={`autogrow ${className}`}
      value={value}
      onChange={(e: ChangeEvent<HTMLTextAreaElement>) => onChange(e.target.value)}
      onKeyDown={(e: KeyboardEvent<HTMLTextAreaElement>) => {
        if (onSubmitKey && e.key === "Enter" && !e.altKey && !e.nativeEvent.isComposing) {
          if (e.shiftKey || e.ctrlKey || e.metaKey) {
            // a line break at the caret: Shift+Return would do it natively, Ctrl/Cmd+Return would not,
            // so do it by hand for all of them (setRangeText keeps the caret; the change is reported)
            e.preventDefault();
            const el = e.currentTarget;
            el.setRangeText("\n", el.selectionStart, el.selectionEnd, "end");
            onChange(el.value);
            return;
          }
          e.preventDefault();
          onSubmitKey();
          return;
        }
        onKeyDown?.(e);
      }}
      {...rest}
    />
  );
}
