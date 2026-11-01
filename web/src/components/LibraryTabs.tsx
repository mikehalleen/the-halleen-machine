import type { LibraryTab } from "../layout/useLibraryTab";

interface Props {
  tab: LibraryTab;
  onTab: (t: LibraryTab) => void;
  bins: number;
  sequences: number;
}

/** BINS | SEQUENCES: the project library's two halves, one at a time. */
export function LibraryTabs({ tab, onTab, bins, sequences }: Props) {
  const one = (t: LibraryTab, label: string, n: number) => (
    <button type="button" role="tab" aria-selected={tab === t} className={`lib-tab ${tab === t ? "is-active" : ""}`} onClick={() => onTab(t)}>
      {label}
      <span className="lib-tab-n">{n}</span>
    </button>
  );
  return (
    <div className="lib-tabs" role="tablist">
      {one("bins", "BINS", bins)}
      {one("sequences", "SEQUENCES", sequences)}
    </div>
  );
}
