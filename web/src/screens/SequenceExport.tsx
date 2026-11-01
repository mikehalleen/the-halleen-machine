import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { downloadUrl, errorText, fetchBins, useBin, useCommand, useExportStatus, useProject, useSequence, useStartExport } from "../api/client";
import { BinPickSheet } from "../components/BinPickSheet";
import { Crumbs } from "../components/Crumbs";
import { libraryCrumb } from "../layout/useLibraryTab";
import { MediaThumb, ratioOf } from "../components/MediaThumb";
import { seqClock } from "../components/SequencePlayer";
import { useToast } from "../components/Toast";
import { Shell } from "../layout/Shell";
import { useMediaViewer } from "../layout/useMediaViewer";

/** An export is named by its file, never "take N": the export writes the sequence, the moment,
 * the size and the rate into it (`20261018-133455_ad_1280x720_24fps.mp4`). */
function exportName(path: string | null | undefined): string {
  return (path ?? "").split(/[\\/]/).pop() || "export";
}

/** The export: what will render, where it lands, the progress, and the past exports. */
export function SequenceExportRoute() {
  const viewer = useMediaViewer();
  const { slug, seqId } = useParams();
  const toast = useToast();
  const project = useProject(slug);
  const seq = useSequence(slug, seqId);
  const status = useExportStatus(slug, seqId);
  const start = useStartExport(slug, seqId);
  const outBin = useBin(slug, seq.data?.output_bin ?? undefined);
  const command = useCommand(slug);
  const back = `/p/${encodeURIComponent(slug ?? "")}/s/${encodeURIComponent(seqId ?? "")}`;
  // where exports land: the sequence's own bin (made at the first export), or any bin not in the cut
  const inCut = new Set((seq.data?.items ?? []).map((i) => i.bin));
  const targets = (project.data?.bins ?? []).filter((b) => !inCut.has(b.id));
  const newBinName = `${seq.data?.name ?? "Sequence"} Exports`;
  const setTarget = (value: string, then?: () => void) =>
    command.mutate(
      { name: "set_sequence_field", body: { sequence: seqId, field: "output_bin", value: value === "" ? null : value } },
      {
        onSuccess: (r) => {
          toast(r.change?.description ?? "changed");
          setPicking(false);
          then?.();
        },
        onError: (e) => toast(errorText(e)),
      },
    );
  // where exports land: the same "which bin?" sheet uploads use, a bin or a new one by name.
  // Opened by Change, or by Export itself while no bin is set -- then the export follows the pick
  const [picking, setPicking] = useState<false | "change" | "export">(false);
  const pickTarget = ({ bin, name }: { bin?: string; name?: string }) => {
    const then = picking === "export" ? doExport : undefined;
    if (bin) return setTarget(bin, then);
    if (!name) {
      // blank: a new "<sequence> Exports" bin, made by the export itself
      setPicking(false);
      return then ? then() : setTarget("");
    }
    command.mutate(
      { name: "create_bin", body: { bin_name: name, workflow: "" } },
      {
        onSuccess: async () => {
          try {
            const made = (await fetchBins(slug ?? "")).find((b) => b.name === name);
            if (made) setTarget(made.id, then);
          } catch (e) {
            toast(errorText(e));
          }
        },
        onError: (e) => toast(errorText(e)),
      },
    );
  };

  const data = seq.data;
  const canvas = project.data?.defaults;
  const running = status.data?.status === "running";
  const pct = Math.round(((status.data?.progress ?? 0) as number) * 100);
  const failed = status.data?.status === "failed";
  const exports = outBin.data?.media ?? []; // newest first
  const unseen = new Set(outBin.data?.bin.unseen ?? []);

  const doExport = () =>
    start.mutate(undefined, {
      onError: (e) => toast(errorText(e)),
    });

  const sampleTitle = data?.items.find((i) => i.kind === "title");

  return (
    <Shell slug={slug} seqId={seqId}>
      <div className="screen" data-screen-label="Export">
        <header className="screen-head">
          <Crumbs
            items={[
              { label: project.data?.name ?? slug ?? "…", to: `/p/${encodeURIComponent(slug ?? "")}`, title: "this project" },
              libraryCrumb(slug, "sequences"),
              { label: data?.name ?? "…", to: back, title: "the sequence" },
              { label: "Export" },
            ]}
          />
          <span className="spacer" />
        </header>
        <div className="screen-body">
          {data && (
            <div className="card card-strong seq-export-summary">
              {/* the sequence's name is in the crumbs; the card leads with where the export lands */}
              {/* no bin yet: nothing to show here -- Export asks where it lands */}
              {data.output_bin && (
                <>
                  <span className="tag">TARGET BIN</span>
                  <div className="row seq-target-title">
                    <b className="card-title">{data.output_bin_name ?? "…"}</b>
                    <button
                      type="button"
                      className="btn-outline seq-pill seq-target-change"
                      onClick={() => setPicking("change")}
                      disabled={command.isPending || running}
                      title="choose the bin the export lands in"
                    >
                      Change
                    </button>
                  </div>
                </>
              )}
              <div className="meta">
                {seqClock(data.total_s)} · {data.items.length} item{data.items.length === 1 ? "" : "s"}
                {data.without_take > 0 && ` · ${data.without_take} without a take`} · mp4
                {canvas?.width && canvas?.height && ` · ${canvas.width}×${canvas.height}`} · other sizes fit to frame
              </div>
            </div>
          )}
          {data && data.without_take > 0 && (
            <div className="card seq-notake-note">
              <span className="tag">BINS WITH NO TAKE YET</span>
              <div className="row">
                <span className="seq-sample-card">
                  {sampleTitle?.shot != null && <span className="seq-card-no">{sampleTitle.shot}</span>}
                  <span>{sampleTitle?.name}</span>
                </span>
                <span className="meta">Play as a title card with the bin's name, at its recipe length. The real take replaces it as soon as the bin has one.</span>
              </div>
            </div>
          )}
          {running && (
            <div className="card seq-render-card">
              <div className="row">
                <span className="row-live" role="status" aria-label="rendering" />
                <b>exporting · {pct}%</b>
              </div>
              <div className="seq-progress">
                <div className="seq-progress-fill" style={{ width: `${pct}%` }} />
              </div>
            </div>
          )}
          {failed && status.data?.error && (
            <div className="card seq-failed-card">
              <b>export failed</b>
              <span className="meta">{status.data.error}</span>
            </div>
          )}
          {data && (
            <div className="seq-past">
              {exports.length === 0 && <div className="hint">{data.output_bin ? "no exports yet" : "no exports yet — Export asks which bin they land in"}</div>}
              {exports.map((m) => (
                // the whole row opens the player; the round pill at the end downloads
                <div
                  key={m.id}
                  className="card seq-past-row is-tappable"
                  role="button"
                  tabIndex={0}
                  title="watch this export"
                  onClick={() => slug && viewer.open(slug, m.id)}
                  onKeyDown={(e) => e.key === "Enter" && slug && viewer.open(slug, m.id)}
                >
                  <MediaThumb slug={slug ?? ""} id={m.id} type={m.type} duration={m.duration_s} size={48} ratio={ratioOf(m)} passive />
                  <span className="seq-row-text">
                    <span className="seq-row-name">
                      {/* breaks offered after each _ and -, so a long name wraps between its parts */}
                      {exportName(m.path)
                        .split(/(?<=[_-])/)
                        .map((part, i) => (
                          <span key={i}>
                            {part}
                            <wbr />
                          </span>
                        ))}
                      {unseen.has(m.id) && <span className="seq-new-dot" role="img" aria-label="new, not yet watched" title="new — not yet watched" />}
                    </span>
                    {m.origin?.note && <span className="seq-row-meta">{m.origin.note}</span>}
                  </span>
                  <a
                    className="seq-dl"
                    href={downloadUrl(slug ?? "", m.id)}
                    download
                    title="download this export"
                    aria-label="Download this export"
                    onClick={(e) => e.stopPropagation()}
                  >
                    ⤓
                  </a>
                </div>
              ))}
            </div>
          )}
        </div>
        <footer className="screen-foot is-split">
          {data?.output_bin && (
            <Link to={`/p/${encodeURIComponent(slug ?? "")}/b/${encodeURIComponent(data.output_bin)}`} className="btn-outline seq-pill seq-open-bin">
              ↗ Open target bin
            </Link>
          )}
          <span className="spacer" />
          {/* the one blue button: this is where the export really starts */}
          <button
            type="button"
            className="btn-primary"
            disabled={!data || data.items.length === 0 || running || start.isPending}
            onClick={() => (data?.output_bin ? doExport() : setPicking("export"))}
          >
            {running ? `Exporting ${pct}%` : "Export"}
          </button>
        </footer>
      </div>
      {picking && data && (
        <BinPickSheet
          title={picking === "export" ? "export into which bin? the export starts when you choose" : "export into which bin?"}
          bins={targets}
          current={data.output_bin}
          blankHint={`blank = a new "${newBinName}" bin, made at export`}
          busy={command.isPending}
          onPick={pickTarget}
          onClose={() => setPicking(false)}
        />
      )}
    </Shell>
  );
}
