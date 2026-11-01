import { useState } from "react";
import { useParams } from "react-router-dom";
import { errorText, useCommand, useProject, useSequence } from "../api/client";
import type { BinCard } from "../api/types";
import { BinFilterRow } from "../components/BinFilterRow";
import { Crumbs } from "../components/Crumbs";
import { libraryCrumb } from "../layout/useLibraryTab";
import { Hint } from "../components/Hint";
import { MediaThumb } from "../components/MediaThumb";
import { useToast } from "../components/Toast";
import { Shell } from "../layout/Shell";
import { useBinFilter } from "../layout/useBinFilter";
import { useCloseTo } from "../layout/useCloseTo";

function detail(b: BinCard): string {
  const kind = (b.tags ?? [])[0] ?? (b.kind === "video" ? "shot" : "bin");
  const m = b.selected_media;
  if (!m) return `${kind} · no take yet`;
  if (m.type === "audio") return `${kind} · audio · a song or a bed goes on the sequence as a layer, not an item`;
  if (m.type === "video") return `${kind} · ${m.take ? `selected take ${m.take}` : "selected clip"}${m.duration_s != null ? ` · ${Number(m.duration_s).toFixed(1)}s` : ""}`;
  return `${kind} · still`;
}

/** Pick bins to append to the sequence: every bin not already in it, newest first, checkboxes. */
export function SequenceAddRoute() {
  const { slug, seqId } = useParams();
  const toast = useToast();
  const project = useProject(slug);
  const seq = useSequence(slug, seqId);
  const command = useCommand(slug);
  const back = `/p/${encodeURIComponent(slug ?? "")}/s/${encodeURIComponent(seqId ?? "")}`;
  const close = useCloseTo(back);
  const [picked, setPicked] = useState<string[]>([]);

  const inSeq = new Set((seq.data?.items ?? []).map((i) => i.bin));
  const outBin = seq.data?.output_bin;
  // the library's own filter row (tag chips, sort, name) over the bins not yet in the sequence
  const available = (project.data?.bins ?? []).filter((b) => !inSeq.has(b.id) && b.id !== outBin);
  const filter = useBinFilter(available, project.data?.tags);
  const candidates = filter.shown;
  const toggle = (id: string) => setPicked((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));

  const add = () => {
    if (!picked.length) return;
    // in tap order: the order you tick them is the order they play
    const order = picked;
    command.mutate(
      { name: "add_sequence_items", body: { sequence: seqId, bins: order } },
      {
        onSuccess: (r) => {
          toast(r.change?.description ?? `added ${order.length}`);
          close();
        },
        onError: (e) => toast(errorText(e)),
      },
    );
  };

  return (
    <Shell slug={slug} seqId={seqId}>
      <div className="screen" data-screen-label="Add bins">
        <header className="screen-head">
          <Crumbs
            items={[
              { label: project.data?.name ?? slug ?? "…", to: `/p/${encodeURIComponent(slug ?? "")}`, title: "this project" },
              libraryCrumb(slug, "sequences"),
              { label: seq.data?.name ?? "…", to: back, title: "the sequence" },
              { label: "Add bins" },
            ]}
          />
          <span className="spacer" />
        </header>
        <div className="screen-body">
          <Hint>Bins already in {seq.data?.name ?? "the sequence"} aren't listed. Each plays its selected take, in the order you tick them.</Hint>
          {project.data && available.length > 0 && <BinFilterRow tags={project.data.tags} filter={filter} />}
          {project.data && available.length === 0 && <div className="hint">every bin is already in the sequence</div>}
          {project.data && available.length > 0 && candidates.length === 0 && <div className="hint">nothing matches that filter</div>}
          <div className="seq-add-list">
            {candidates.map((b) => {
              const at = picked.indexOf(b.id);
              const on = at >= 0;
              const audio = b.selected_media?.type === "audio";
              return (
                <label key={b.id} className={`card seq-add-row ${on ? "is-on" : ""} ${audio ? "is-off" : ""}`} title={audio ? "audio cannot be a sequence item; the agent lays it under the sequence as a layer" : undefined}>
                  <input type="checkbox" className="seq-add-check" checked={on} disabled={audio} onChange={() => toggle(b.id)} aria-label={`Add ${b.name}`} />
                  <span className="seq-add-pos" aria-hidden="true">{on ? at + 1 : ""}</span>
                  <MediaThumb slug={slug ?? ""} id={b.selected_media?.id} type={b.selected_media?.type} size={44} passive dashed={!b.selected_media} />
                  <span className="seq-row-text">
                    <span className="seq-row-name">
                      {b.shot != null && <span className="shot-no">{b.shot}</span>}
                      {b.name}
                    </span>
                    <span className="seq-row-meta">{detail(b)}</span>
                  </span>
                </label>
              );
            })}
          </div>
        </div>
        <footer className="screen-foot is-split">
          <button type="button" className="btn-outline" onClick={close}>
            Cancel
          </button>
          <span className="spacer" />
          <button type="button" className="btn-primary" disabled={!picked.length || command.isPending} onClick={add}>
            {command.isPending ? "adding…" : `Add ${picked.length} to end`}
          </button>
        </footer>
      </div>
    </Shell>
  );
}
