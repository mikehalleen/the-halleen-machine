/**
 * Keeps the app the height of the *visual* viewport, so the bottom of the shell (the composer,
 * the kickoff notes, any sheet) stays above the on-screen keyboard on a phone.
 *
 * `100%` / `100dvh` follow the layout viewport, which iOS never shrinks for the keyboard and
 * Android only shrinks with `interactive-widget=resizes-content` (set in index.html). Safari
 * instead scrolls the visual viewport to reveal the focused field, which drags the shell's
 * head off the top and leaves the composer under the keyboard. So: publish
 * `--app-height` from `visualViewport` (base.css sizes `.shell` and pins the toast with it) and
 * re-pin the visual viewport to the top whenever the keyboard has it shifted.
 */
export function trackVisualViewport(): void {
  const vv = window.visualViewport;
  if (!vv) return;
  const root = document.documentElement;
  let raf = 0;

  const apply = () => {
    raf = 0;
    // pinch-zoom shrinks the visual viewport too; scaling back up keeps the layout untouched
    // while zoomed and yields the true keyboard-reduced height at scale 1
    const height = Math.round(vv.height * vv.scale);
    if (height >= window.innerHeight - 1) {
      root.style.removeProperty("--app-height"); // nothing open: back to the stylesheet's 100%
      return;
    }
    root.style.setProperty("--app-height", `${height}px`);
    // the keyboard (not a pinch) has the visual viewport shifted down: bring it back so the
    // shrunken shell fills exactly what is visible
    if (vv.scale === 1 && (vv.offsetTop > 0 || window.scrollY > 0)) window.scrollTo(0, 0);
  };
  const schedule = () => {
    if (!raf) raf = requestAnimationFrame(apply);
  };

  vv.addEventListener("resize", schedule);
  vv.addEventListener("scroll", schedule);
  window.addEventListener("resize", schedule);
  apply();
}
