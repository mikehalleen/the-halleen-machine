/**
 * The padlock that says whether a private project's pictures are showing: shut while the veil
 * is down, shackle swung open while they are visible. One drawing, used by the system bar's
 * phone toggle and by the full-screen viewer's, so the two can never drift apart.
 *
 * The size comes from CSS (the attributes are only the fallback), which is how the open state
 * reads a little larger than the shut one -- the louder mark is the one that means "on show".
 */
export function VeilGlyph({ open }: { open: boolean }) {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <rect x="3" y="7" width="10" height="7" rx="1.5" fill="currentColor" />
      {open ? (
        /* open shackle: swung up and to the side */
        <path d="M5 7V4.5a3 3 0 0 1 6 0" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" transform="translate(4.5 -1) rotate(20 8 5)" />
      ) : (
        <path d="M5 7V4.5a3 3 0 0 1 6 0V7" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      )}
    </svg>
  );
}
