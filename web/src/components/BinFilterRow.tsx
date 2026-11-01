import type { useBinFilter } from "../layout/useBinFilter";

interface Props {
  /** The project's vocabulary: the defaults, then every tag a bin carries. Unused here beyond
   * the filter's own `inUse`, which is this list narrowed to tags some bin carries. */
  tags: string[];
  filter: ReturnType<typeof useBinFilter>;
  className?: string;
}

/** `all` plus one chip per tag in use, single select, and the name search. The sidebar and the
 * phone library screen draw the same row. */
export function BinFilterRow({ filter, className }: Props) {
  return (
    <div className={`row lib-filters ${className ?? ""}`}>
      {["all", ...filter.inUse].map((t) => (
        <button key={t} type="button" className={`chip ${filter.tag === t ? "is-on" : ""}`} onClick={() => filter.setTag(t)}>
          {t}
        </button>
      ))}
      <input
        className="lib-search"
        type="search"
        value={filter.query}
        placeholder="filter…"
        aria-label="Filter bins by name"
        onChange={(e) => filter.setQuery(e.target.value)}
      />
    </div>
  );
}
