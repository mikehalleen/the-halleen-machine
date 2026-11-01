/** React Router stamps its entry index on history.state; above zero means back stays inside the app. */
export function canGoBack(): boolean {
  const idx = (window.history.state as { idx?: number } | null)?.idx;
  return typeof idx === "number" && idx > 0;
}
