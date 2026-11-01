import { NEW_FILTER, type BinSort, type useBinFilter } from "../layout/useBinFilter";

interface Props {
  /** The project's vocabulary: the defaults, then every tag a bin carries. Unused here beyond
   * the filter's own `inUse`, which is this list narrowed to tags some bin carries. */
  tags: string[];
  filter: ReturnType<typeof useBinFilter>;
  className?: string;
}

/** `all` plus one chip per tag in use, single select, the sort, and the name search. The sidebar and the
 * phone library screen draw the same row. */
export function BinFilterRow({ filter, className }: Props) {
  return (
    <div className={`row lib-filters ${className ?? ""}`}>
      <button type="button" className={`chip ${filter.tag === "all" ? "is-on" : ""}`} onClick={() => filter.setTag("all")}>
        all
      </button>
      <button
        type="button"
        className={`chip chip-new ${filter.tag === NEW_FILTER ? "is-on" : ""}`}
        onClick={() => filter.setTag(NEW_FILTER)}
        title={filter.fresh ? "only bins with new takes to look at" : "nothing new to look at right now"}
      >
        {filter.fresh > 0 && <span className="row-new" aria-hidden="true">{filter.fresh}</span>} new
      </button>
      {filter.inUse.map((t) => (
        <button key={t} type="button" className={`chip ${filter.tag === t ? "is-on" : ""}`} onClick={() => filter.setTag(t)}>
          {t}
        </button>
      ))}
      <select className="lib-sort" value={filter.sort} aria-label="Sort bins" title="sort bins" onChange={(e) => filter.setSort(e.target.value as BinSort)}>
        <option value="updated">updated</option>
        <option value="created">created</option>
        <option value="name">alphabetical</option>
        {filter.hasShots && <option value="shot">shot #</option>}
      </select>
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
