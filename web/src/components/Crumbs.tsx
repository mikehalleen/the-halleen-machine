import { Link } from "react-router-dom";
import { EditableTitle } from "./EditableTitle";

export interface Crumb {
  label: string;
  /** Where this crumb goes. The last crumb is where you are and has none. */
  to?: string;
  title?: string;
  /** The last crumb only: it is a name that edits in place — tap to rename, save on leave. */
  rename?: { onSave: (next: string) => void; ariaLabel: string; disabled?: boolean };
  onClick?: () => void;
}

/**
 * Every screen's place line: `Projects ‹ Memes ‹ newtoold ‹ Generate`. One rule across the app,
 * in place of a per-screen back arrow: tap a crumb to go there; the last one is where you are,
 * and renames when it is a name. The last crumb keeps the title weight. A line too long for the
 * head loses its start, never its tail: `…Ad ‹ Sequences ‹ Low End Theory`.
 */
export function Crumbs({ items }: { items: Crumb[] }) {
  const last = items.length - 1;
  return (
    <nav className="screen-crumbs" aria-label="Where you are">
      <span className="screen-crumbs-line">
      {items.map((c, i) => {
        const here = i === last;
        return (
          <span key={i} className="crumb">
            {i > 0 && (
              <span className="crumb-sep" aria-hidden="true">
                ‹
              </span>
            )}
            {here && c.rename ? (
              <EditableTitle className="screen-title" value={c.label} onSave={c.rename.onSave} ariaLabel={c.rename.ariaLabel} disabled={c.rename.disabled} />
            ) : here || !c.to ? (
              <b className="screen-title" aria-current="page">
                {c.label}
              </b>
            ) : (
              <Link className="crumb-link" to={c.to} title={c.title} onClick={c.onClick}>
                {c.label}
              </Link>
            )}
          </span>
        );
      })}
      </span>
    </nav>
  );
}
