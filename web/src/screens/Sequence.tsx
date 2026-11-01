import { useCallback, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { errorText, useCommand, useExportStatus, useProject, useSequence } from "../api/client";
import type { SequenceCard } from "../api/types";
import { Crumbs } from "../components/Crumbs";
import { libraryCrumb } from "../layout/useLibraryTab";
import { SequenceList } from "../components/SequenceList";
import { SequencePlayer } from "../components/SequencePlayer";
import { useToast } from "../components/Toast";
import { Shell } from "../layout/Shell";
import { useBreakpoint } from "../layout/useBreakpoint";
import { useDeleteSequence } from "../layout/useDeleteSequence";

/**
 * The sequence: an ordered list of bins that plays each bin's selected take. Header with the
 * name (renamed inline) and clone; the stepping player; the rows; add and export in the foot.
 * One focus joins the player and the list: a tap on a row opens it and parks the player there,
 * and playback opens the row it is on. Agent edits land live through the project event; the feed
 * says what moved.
 */
export function SequenceRoute() {
  const { slug, seqId } = useParams();
  const { wide } = useBreakpoint();
  const navigate = useNavigate();
  const toast = useToast();
  const project = useProject(slug);
  const seq = useSequence(slug, seqId);
  const exportStatus = useExportStatus(slug, seqId);
  const command = useCommand(slug);
  const deleteSeq = useDeleteSequence(slug, project.data?.bins, seqId);

  // the item in focus: the player is parked on it and its row is open
  const [current, setCurrent] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const onOpen = useCallback((bin: string | null) => {
    setOpen(bin);
    if (bin) setCurrent(bin);
  }, []);
  const onCurrent = useCallback((bin: string) => {
    setCurrent(bin);
    setOpen(bin);
  }, []);

  const run = (name: string, body: Record<string, unknown>, done?: (desc: string) => void) =>
    new Promise<void>((resolve, reject) =>
      command.mutate(
        { name, body },
        {
          onSuccess: (r) => {
            done?.(r.change?.description ?? "");
            resolve();
          },
          onError: (e) => {
            toast(errorText(e));
            reject(e);
          },
        },
      ),
    );

  const rename = (name: string) => void run("set_sequence_field", { sequence: seqId, field: "name", value: name });
  const clone = () =>
    command.mutate(
      { name: "clone_sequence", body: { sequence: seqId } },
      {
        onSuccess: (r) => toast(r.change?.description ?? "cloned"),
        onError: (e) => toast(errorText(e)),
      },
    );

  const data = seq.data;
  const base = `/p/${encodeURIComponent(slug ?? "")}/s/${encodeURIComponent(seqId ?? "")}`;
  const exporting = exportStatus.data?.status === "running";
  const pct = Math.round(((exportStatus.data?.progress ?? 0) as number) * 100);
  const canvas = project.data?.defaults;
  const canvasRatio = canvas?.width && canvas?.height ? canvas.width / canvas.height : 16 / 9;
  const card: SequenceCard | undefined = (project.data?.sequences ?? []).find((s) => s.id === seqId);

  return (
    <Shell slug={slug} seqId={seqId}>
      <div className="screen seq-screen" data-screen-label="Sequence">
        <header className="screen-head">
          <Crumbs
            items={[
              { label: project.data?.name ?? slug ?? "…", to: `/p/${encodeURIComponent(slug ?? "")}`, title: "this project" },
              libraryCrumb(slug, "sequences"),
              { label: data?.name ?? "…", rename: { onSave: rename, ariaLabel: "Sequence name", disabled: !data } },
            ]}
          />
          <span className="spacer" />
          <button type="button" className="link" disabled={!data || command.isPending} onClick={clone} title="a copy with the same items, trims and lengths; its exports get their own bin">
            ⧉ clone
          </button>
        </header>
        <div className="screen-body seq-body">
          {seq.isLoading && <div className="hint">loading…</div>}
          {seq.error && <div className="hint">could not load this sequence</div>}
          {data && slug && seqId && (
            <>
              <SequencePlayer slug={slug} items={data.items} layer={data.agent as Record<string, unknown> | undefined} totalS={data.total_s} current={current} onCurrent={onCurrent} canvasRatio={canvasRatio} />
              {data.without_take > 0 && <div className="meta seq-phone-meta">{data.without_take} without a take · plays as a title card</div>}
              {data.items.length === 0 && <div className="hint">no items yet — ＋ Add bins below. Each plays its selected take.</div>}
              <SequenceList
                slug={slug}
                seqId={seqId}
                items={data.items}
                wide={wide}
                open={open}
                onOpen={onOpen}
                onReorder={(order) => run("reorder_sequence", { sequence: seqId, order })}
                onRemove={(bin) => void run("remove_sequence_item", { sequence: seqId, bin }, (d) => toast(d))}
                onStill={(bin, seconds) => void run("set_item_still", { sequence: seqId, bin, still_s: seconds })}
                onDropBin={wide ? (bin, at) => void run("add_sequence_items", { sequence: seqId, bins: [bin], ...(at === null ? {} : { at }) }, (d) => toast(d)) : undefined}
              />
            </>
          )}
        </div>
        <footer className="screen-foot seq-foot">
          <Link to={`${base}/add`} className="btn-dashed seq-foot-add">
            ＋ Add bins
          </Link>
          <span className="spacer" />
          {/* opens the export screen; the blue button lives there, where the export really starts */}
          <button type="button" className={`btn-outline btn-open-recipe seq-export-btn ${exporting ? "is-exporting" : ""}`} disabled={!data || data.items.length === 0} onClick={() => navigate(`${base}/export`)} title="the export: where it lands, past exports">
            {exporting && <span className="seq-export-bar" style={{ width: `${pct}%` }} aria-hidden="true" />}
            <span className="seq-export-label">{exporting ? `Exporting ${pct}% ▸` : "Exports ▸"}</span>
          </button>
        </footer>
        {card && deleteSeq.sheet}
      </div>
    </Shell>
  );
}
