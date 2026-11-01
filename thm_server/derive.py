"""Pure derivations from records to the shapes the screens read. No HTTP here."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from thm_core import Bin, Capacity, Change, Env, Generation, Media, Message, Project, ProjectStore, resolve_references, workflow_capacity
from thm_core.commands import full_quality, is_note, item_duration, item_frames, item_kind, item_media, layer_span_under, open_notes, sequence_fps, trim_dropped
from thm_core.models import DEFAULT_TAGS, Post, Sequence
from thm_core.workflow_capabilities import scan_video_workflow_file, scan_workflow_file
from thm_core.workflow_names import (
    DEFAULT_PROJECT_WORKFLOW_FILENAME,
    DEFAULT_VIDEO_WORKFLOW_FILENAME,
    resolve_curated_workflow,
    workflow_kind_for_path,
)

from .schemas import (
    BinCard,
    BinDetail,
    BinRef,
    CapacityOut,
    ChannelOut,
    FeedEntry,
    RenderRef,
    ProjectCard,
    ProjectDetail,
    ReferenceOut,
    LayerUnderItem,
    MovedItemOut,
    PostOut,
    SequenceCard,
    SequenceItemOut,
    SequenceOut,
    ShotNeedOut,
    ShotOut,
    ShotRefOut,
    ThumbRef,
    WorkflowInfo,
)

OWED_DECISIONS = ("pending", "proposed")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- newest-first views ---------------------------------------------------------
#
# Lists of *content* read newest first: projects on Home, bins in a project, media in a bin.
# "Newest" is the newest thing inside the container, never when the container itself was made.
# References in a bin keep their recorded order: that order is the user's, and it names tokens.


def newest_first(media: list[Media]) -> list[Media]:
    """Stable: equal timestamps keep the order they were added, reversed."""
    return sorted(reversed(media), key=lambda m: m.created, reverse=True)


def latest_created(project: Project, media_ids) -> str:
    return max((project.media_by_id[m].created for m in media_ids if m in project.media_by_id), default="")


def bin_latest(project: Project, bin_: Bin) -> str:
    """When the bin last changed: an edit or a landing, else its newest content, else when it was made."""
    return max(bin_.updated, latest_created(project, bin_.media), bin_.created)


def ordered_bins(project: Project) -> list[Bin]:
    bins = list(project.bins_by_id.values())
    return sorted(reversed(bins), key=lambda b: bin_latest(project, b), reverse=True)


def effective_selected(project: Project, bin_: Bin) -> Media | None:
    """The media the bin leads with: its selected when that still exists, else its newest."""
    if bin_.selected and bin_.selected in project.media_by_id and bin_.selected in bin_.media:
        return project.media_by_id[bin_.selected]
    held = [project.media_by_id[m] for m in bin_.media if m in project.media_by_id]
    return newest_first(held)[0] if held else None


def bin_kind(bin_: Bin, env: Env) -> str | None:
    if not bin_.workflow or not env.capacity(bin_.workflow).found:
        return None
    return env.kind(bin_.workflow)


def bin_card(project: Project, bin_: Bin, env: Env) -> BinCard:
    held = [project.media_by_id[m] for m in bin_.media if m in project.media_by_id]
    return BinCard(
        **{**bin_.model_dump(), "updated": bin_latest(project, bin_)},
        media_types=sorted({m.type for m in held}),
        take_count=sum(1 for m in held if m.origin.type == "generation" and m.origin.bin == bin_.id),
        selected_media=effective_selected(project, bin_),
        kind=bin_kind(bin_, env),
    )


def bin_cards(project: Project, env: Env) -> list[BinCard]:
    return [bin_card(project, b, env) for b in ordered_bins(project)]


# -- sequences ------------------------------------------------------------------


def sequence_total(project: Project, seq: Sequence) -> float:
    return round(sum(item_duration(project, i) for i in seq.items), 3)


def output_bin_of(project: Project, seq: Sequence):
    """The bin exports land in, or None when the sequence has none or points at a bin that is gone."""
    return project.bins_by_id.get(seq.output_bin) if seq.output_bin else None


def sequence_card(project: Project, seq: Sequence) -> SequenceCard:
    out = output_bin_of(project, seq)
    plays = [(i, item_media(project, i)) for i in seq.items]
    # the card's tile: the first item that plays a picture or a clip
    thumb = next((m for _, m in plays if m is not None and m.type in ("image", "video")), None)
    fresh = sum(1 for i, m in plays if m is not None and (b := project.bins_by_id.get(i.bin)) is not None and m.id in b.unseen)
    return SequenceCard(
        id=seq.id, name=seq.name, output_bin=out.id if out is not None else None, items=len(seq.items), total_s=sequence_total(project, seq),
        thumb=thumb, bins=[i.bin for i in seq.items], fresh=fresh,
    )


def sequence_cards(project: Project) -> list[SequenceCard]:
    return [sequence_card(project, s) for s in project.sequences]


def channel_record(env: Env | None, channel_id: str) -> ChannelOut | None:
    """The connected channel behind an id, as the server knows it now; None when none is."""
    if not channel_id or env is None or env.youtube_channel is None:
        return None
    info = env.youtube_channel(channel_id)
    return ChannelOut(**info) if info else None


def post_out(project: Project, post: Post, env: Env | None = None) -> PostOut:
    bin_ = project.bins_by_id.get(post.bin)
    channel = channel_record(env, post.channel)
    return PostOut(
        **post.model_dump(),
        bin_name=bin_.name if bin_ is not None else "",
        media_record=project.media_by_id.get(post.media),
        thumbnail_record=project.media_by_id.get(post.thumbnail) if post.thumbnail else None,
        channel_handle=(channel.handle or channel.title) if channel is not None else "",
    )


def post_outs(project: Project, env: Env | None = None) -> list[PostOut]:
    """Every post, newest first."""
    return [post_out(project, p, env) for p in sorted(project.posts, key=lambda p: p.created, reverse=True)]


def sequence_out(project: Project, seq: Sequence) -> SequenceOut:
    """The sequence screen's shape: every item joined to its bin and the take that plays, with
    its duration and running start, so the list, the player and the stitcher read one thing."""
    items: list[SequenceItemOut] = []
    start = 0.0
    start_frame = 0
    without = 0
    fps = sequence_fps(project, seq)
    layer_media = {lay.id: project.media_by_id.get(lay.media) for lay in seq.layers}
    for i, item in enumerate(seq.items):
        bin_ = project.bins_by_id.get(item.bin)
        media = item_media(project, item)
        kind = item_kind(project, item)
        if kind == "title":
            without += 1
        dur = item_duration(project, item)
        frames = item_frames(project, item, fps)
        offsets = item.mix.layers if item.mix is not None else {}
        under: dict[str, LayerUnderItem] = {}
        for lay in seq.layers:
            span = layer_span_under(lay, layer_media.get(lay.id), start_frame, frames, fps)
            muted = lay.id in offsets and offsets[lay.id] is None
            under[lay.id] = LayerUnderItem(
                source_in_s=span[0] if span else None, source_out_s=span[1] if span else None,
                db=None if muted else float(offsets.get(lay.id) or 0.0), muted=muted,
            )
        reviewed = item.mix.reviewed_start_frame if item.mix is not None else None
        items.append(
            SequenceItemOut(
                bin=item.bin,
                position=i + 1,
                shot=bin_.shot if bin_ is not None else None,
                name=bin_.name if bin_ is not None else item.bin,
                tags=list(bin_.tags) if bin_ is not None else [],
                media=media,
                kind=kind,
                duration_s=round(dur, 3),
                start_s=round(start, 3),
                head_s=item.head_s,
                tail_s=item.tail_s,
                still_s=item.still_s,
                trim_dropped=trim_dropped(project, item),
                agent=dict(item.agent),
                start_frame=start_frame,
                frames=frames,
                mix=item.mix,
                layers=under,
                layer_moved=reviewed is not None and reviewed != start_frame,
            )
        )
        start += dur
        start_frame += frames
    out = output_bin_of(project, seq)
    held = newest_first([project.media_by_id[m] for m in (out.media if out is not None else []) if m in project.media_by_id])
    return SequenceOut(
        id=seq.id,
        name=seq.name,
        output_bin=out.id if out is not None else None,
        output_bin_name=out.name if out is not None else "",
        items=items,
        agent=dict(seq.agent),
        layers=list(seq.layers),
        fps=fps,
        total_frames=start_frame,
        total_s=round(start, 3),
        without_take=without,
        last_export=held[0] if held else None,
        created=seq.created,
        updated=seq.updated,
    )


def moved_items(project: Project) -> list[MovedItemOut]:
    """Every sequence item the agent QC'd against the layers at a start frame that has since
    changed (something before it was trimmed, added or removed): the resume summary lists them."""
    out: list[MovedItemOut] = []
    for seq in project.sequences:
        if not seq.layers:
            continue
        so = sequence_out(project, seq)
        for row in so.items:
            if row.layer_moved and row.mix is not None and row.mix.reviewed_start_frame is not None:
                out.append(MovedItemOut(sequence=seq.id, sequence_name=seq.name, bin=row.bin, name=row.name, position=row.position, reviewed_start_frame=row.mix.reviewed_start_frame, start_frame=row.start_frame))
    return out


def project_tags(project: Project) -> list[str]:
    """The tags the explorer offers: the defaults in their order, then everything else in use."""
    used = {t for b in project.bins_by_id.values() for t in b.tags}
    return list(DEFAULT_TAGS) + sorted(used - set(DEFAULT_TAGS))


def project_latest(store: ProjectStore, project: Project) -> str:
    """The newest media in the project; a project with none falls back to its file's mtime."""
    latest = latest_created(project, project.media_by_id.keys())
    if latest:
        return latest
    try:
        return _iso(store.project_path(project).stat().st_mtime)
    except OSError:
        return ""


def project_card(store: ProjectStore, project: Project, version: int) -> ProjectCard:
    # one picture stands for the project: the newest bin that leads with something
    hero: ThumbRef | None = None
    if not project.private:  # a private project shows no cover on the list
        for bin_ in ordered_bins(project):
            m = effective_selected(project, bin_)
            if m is not None:
                hero = ThumbRef(id=m.id, type=m.type)
                break
    try:
        modified = _iso(store.project_path(project).stat().st_mtime)
    except OSError:
        modified = ""
    return ProjectCard(
        slug=project.slug,
        id=project.id,
        name=project.name,
        bin_count=len(project.bins_by_id),
        video_count=sum(1 for m in project.media_by_id.values() if m.type == "video"),
        image_count=sum(1 for m in project.media_by_id.values() if m.type == "image"),
        hero=hero,
        private=project.private,
        modified=modified,
        version=version,
        canvas=f"{project.defaults.width}x{project.defaults.height}" if project.defaults.configured else None,
        image_workflow=project.defaults.image_workflow,
        video_workflow=project.defaults.video_workflow,
        agent_session=project.agent_session,
        # a blocker is agent prose about the project; a private project's stays inside it
        blocker=current_blocker(project, store.read_messages(project)) if project.agent_session is not None and not project.private else None,
        pending_changes=pending_changes(store, project) if project.agent_session is not None else 0,
        open_notes=len(open_notes(store, project)),
    )


def pending_changes(store: ProjectStore, project: Project) -> int:
    """Decisions the user owes *now*: pending (applied, awaiting a verdict) and proposed (waiting
    to be applied) cards of the session that is waiting or live -- a render's card only once its
    take is in, not while it waits or renders. A card left over from an ended
    session stays in the feed as history but is not an open item: nobody is waiting on it, and a
    count that kept growing across sessions read as inaccurate the moment auto mode stopped
    making cards."""
    sess = project.agent_session
    if sess is None or sess.status not in ("waiting", "live"):
        return 0
    # a render card owes its verdict once there is a take to judge: while the render waits
    # ("later") or runs -- or if it failed with nothing -- nobody is waiting on the user (the
    # panel's own count, Feed.isOpenCard, reads it the same way)
    unfinished = {g.id for g in store.read_generations(project) if not g.outputs}
    return sum(
        1 for c in store.read_changes(project).values()
        if c.session == sess.id and c.decision is not None and c.decision.status in OWED_DECISIONS
        and not (c.generation and c.generation in unfinished)
    )


def session_changes(store: ProjectStore, project: Project) -> int:
    """Everything the current session changed, whatever its status: the bar's ``31 changes``
    link after a session ends is how the transcript is found again."""
    sess = project.agent_session
    if sess is None:
        return 0
    return sum(1 for c in store.read_changes(project).values() if c.session == sess.id)


def current_blocker(project: Project, messages: list[Message]) -> Message | None:
    """The live session's newest blocker not yet answered by a user message or a decision."""
    sess = project.agent_session
    if sess is None or sess.status != "live":
        return None
    for m in reversed(messages):
        if m.kind in ("user", "decision"):
            return None
        if m.kind == "blocker" and m.session == sess.id:
            return m
    return None


def project_detail(store: ProjectStore, project: Project, version: int, env: Env) -> ProjectDetail:
    blocker = current_blocker(project, store.read_messages(project)) if project.agent_session is not None else None
    return ProjectDetail(
        schema_version=project.schema_version,
        id=project.id,
        name=project.name,
        slug=project.slug,
        comfy=project.comfy,
        defaults=project.defaults,
        counters=dict(project.counters),
        brief=project.brief,
        private=project.private,
        youtube_channel=project.youtube_channel,
        youtube_channel_record=channel_record(env, project.youtube_channel),
        configured=project.defaults.configured,
        agent_session=project.agent_session,
        bins=bin_cards(project, env),
        sequences=sequence_cards(project),
        posts=post_outs(project, env),
        tags=project_tags(project),
        media_count=len(project.media_by_id),
        pending_changes=pending_changes(store, project),
        open_notes=len(open_notes(store, project)),
        version=version,
        blocker=blocker,
        session_changes=session_changes(store, project),
    )


# -- the session feed -----------------------------------------------------------


def _ref_ids(refs) -> set[str]:
    return {str(r.get("id")) for r in (refs or []) if isinstance(r, dict) and r.get("id")}


def card_media(project: Project, change: Change | None, generation: Generation | None) -> Media | None:
    """The media a change produced or bound, for the card preview. Set differences, never 'the last one'."""
    if change is None:
        return None
    media_id: str | None = None
    before, after = change.before or {}, change.after or {}
    if change.target.type == "media":
        media_id = change.target.id
    elif change.command == "set_bin_field" and "selected" in after:
        media_id = after.get("selected") or None
    elif change.command == "set_shot_field" and "selected_take" in after:  # a schema-3 card
        media_id = after.get("selected_take") or None
    elif change.command == "bind_reference":
        known = _ref_ids(before.get("references"))
        new = [r for r in (after.get("references") or []) if isinstance(r, dict) and str(r.get("id")) not in known]
        media_id = str(new[-1].get("media")) if new and new[-1].get("media") else None
    elif change.command == "unbind_reference":
        kept = _ref_ids(after.get("references"))
        gone = [r for r in (before.get("references") or []) if isinstance(r, dict) and str(r.get("id")) not in kept]
        media_id = str(gone[-1].get("media")) if gone and gone[-1].get("media") else None
    elif change.command in ("add_media_to_bin", "add_media_to_item"):
        had = set(before.get("media") or [])
        new_media = [m for m in (after.get("media") or []) if m not in had]
        media_id = str(new_media[-1]) if new_media else None
    if media_id is None and generation is not None and generation.outputs:
        media_id = generation.outputs[-1]
    return project.media_by_id.get(media_id) if media_id else None


def card_bin(project: Project, change: Change | None, media: Media | None) -> BinRef | None:
    bin_id: str | None = None
    if change is not None and change.target.type == "bin":
        bin_id = change.target.id
    elif media is not None and media.origin.bin:
        bin_id = media.origin.bin
    bin_ = project.bins_by_id.get(bin_id) if bin_id else None
    return BinRef(id=bin_.id, name=bin_.name) if bin_ is not None else None


def _shot_needs(project: Project, needs: list) -> list[ShotNeedOut]:
    """Each declared need against the project now: the source bin by id, or by name when it was
    still to be made; ready when that bin leads with a take (its selected, else its newest)."""
    out: list[ShotNeedOut] = []
    for n in needs:
        if not isinstance(n, dict):
            continue
        src = project.bins_by_id.get(str(n.get("bin") or ""))
        if src is None and n.get("bin_name"):
            wanted = str(n["bin_name"]).strip().lower()
            src = next((b for b in project.bins_by_id.values() if b.name.strip().lower() == wanted), None)
        lead = effective_selected(project, src) if src is not None else None
        out.append(ShotNeedOut(bin=src.id if src is not None else None, bin_name=src.name if src is not None else str(n.get("bin_name") or ""), what=str(n.get("what") or ""), ready=lead is not None, media=lead.id if lead is not None else None))
    return out


def shots_out(project: Project, change: Change, env: Env | None) -> list[ShotOut]:
    """A propose_shots change's items as the card reads them: the proposal, plus the outcome the
    decision wrote into the payload (dropped, edited, the bin made, the child change that rendered)."""
    out: list[ShotOut] = []
    for i, item in enumerate((change.payload or {}).get("items") or []):
        if not isinstance(item, dict):
            continue
        target = project.bins_by_id.get(str(item.get("bin_id") or item.get("bin") or ""))
        workflow = item.get("workflow") or (target.workflow if target is not None else None) or project.defaults.image_workflow or None
        refs: list[ShotRefOut] = []
        for mid in item.get("references") or []:
            m = project.media_by_id.get(str(mid))
            if m is None:
                continue
            ref_bin = project.bins_by_id.get(m.origin.bin or "") if m.origin is not None else None
            refs.append(ShotRefOut(media=m.id, type=m.type, label=ref_bin.name if ref_bin is not None else m.id))
        # a card that names no references renders with the bin's own: show those, so the card
        # never reads as a render from nothing
        from_bin = not refs and target is not None and bool(target.references)
        if from_bin:
            for r in target.references:
                m = project.media_by_id.get(r.media)
                if m is not None:
                    refs.append(ShotRefOut(media=m.id, type=m.type, label=r.handle))
        out.append(
            ShotOut(
                index=i,
                name=str(item.get("name") or ""),
                shot=target.shot if target is not None else None,
                description=str(item.get("description") or ""),
                has_prompt=item.get("prompt") is not None,
                workflow=workflow,
                kind=env.kind(workflow) if env is not None and workflow and env.capacity(workflow).found else None,
                length_s=item.get("length_s"),
                quarter=bool(item.get("quarter")),
                draft=bool(item.get("draft")),
                full_quality=bool(env is not None and workflow and env.capacity(workflow).found and full_quality(project, workflow, {"quarter": item.get("quarter"), "draft": item.get("draft"), **(item.get("params") or {})}, env)),
                takes=sum(1 for t in (target.media if target is not None else []) if t in project.media_by_id and project.media_by_id[t].origin.type == "generation"),
                held=bool((change.payload or {}).get("hold")),
                references=refs,
                references_from_bin=from_bin,
                needs=_shot_needs(project, item.get("needs") or []),
                dropped=bool(item.get("dropped")),
                edited=bool(item.get("edited")),
                bin=BinRef(id=target.id, name=target.name) if target is not None else None,
                change=item.get("change") or None,
                error=item.get("error") or None,
            )
        )
    return out


def feed_entry(
    project: Project, message: Message, change: Change | None, generation: Generation | None, env: Env | None = None, open_ids: set[str] | None = None
) -> FeedEntry:
    """``open_ids`` is the session's open notes when the caller has them (the feed); without it
    (a message just posted, on its way out as an event) a note of the live session is open."""
    media = card_media(project, change, generation)
    # the card is built from before/after here; the client never reads them, and a project-level
    # change logged before diffs were compacted can carry the whole library on both sides
    slim = change.model_copy(update={"before": {}, "after": {}}) if change is not None else None
    shots = shots_out(project, change, env) if change is not None and change.command == "propose_shots" else None
    render = RenderRef(generation=generation.id, status=generation.status) if change is not None and change.command == "generate" and generation is not None else None
    sess = project.agent_session
    answer = sess.answered.get(message.id) if sess is not None and message.session == sess.id else None
    if open_ids is not None:
        still_open = message.id in open_ids
    else:
        still_open = answer is None and sess is not None and sess.status == "live" and message.session == sess.id and is_note(message)
    return FeedEntry(message=message, answer=answer, open=still_open, change=slim, media=media, bin=card_bin(project, change, media), shots=shots, render=render)


def feed_entries(store: ProjectStore, project: Project, since: int = 0, env: Env | None = None) -> tuple[list[FeedEntry], int]:
    """Messages past ``since`` joined to their changes and card media; the cursor is the inbox cursor."""
    messages, cursor = store.read_inbox(project, since)
    changes = store.read_changes(project) if any(m.change for m in messages) else {}
    gens = {g.id: g for g in store.read_generations(project)} if any(c.generation for c in changes.values()) else {}
    entries: list[FeedEntry] = []
    open_ids = {n.id for n in open_notes(store, project)}
    for m in messages:
        change = changes.get(m.change) if m.change else None
        gen = gens.get(change.generation) if change is not None and change.generation else None
        entries.append(feed_entry(project, m, change, gen, env, open_ids))
    return entries, cursor


def capacity_out(cap: Capacity) -> CapacityOut:
    return CapacityOut(
        found=cap.found,
        image_slots=cap.image_slots,
        video_slots=cap.video_slots,
        supports_start_frame=cap.supports_start_frame,
        supports_end_frame=cap.supports_end_frame,
        addresses_by_token=cap.addresses_by_token,
        slot_node_ids=list(cap.slot_node_ids),
        video_slot_node_ids=list(cap.video_slot_node_ids),
        supports_draft=cap.supports_draft,
        supports_size=cap.supports_size,
        kind=cap.kind,
        error=cap.error,
    )


def reference_sub_label(cap: Capacity, media_type: str | None, ordinal: int | None) -> str | None:
    """``start`` / ``end`` for positional video workflows, ``unused`` beyond capacity, else None."""
    if ordinal is None or media_type is None:
        return None
    if ordinal > cap.slots_for(media_type):
        return "unused"
    positional = media_type == "image" and not cap.slot_node_ids and (cap.supports_start_frame or cap.supports_end_frame)
    if not positional:
        return None
    if cap.supports_start_frame:
        if ordinal == 1:
            return "start"
        if ordinal == 2 and cap.supports_end_frame:
            return "end"
        return None
    return "end" if ordinal == 1 else None


def workflow_info(name: str, workflows_dir) -> WorkflowInfo | None:
    wdir = Path(workflows_dir)
    path = resolve_curated_workflow(name, wdir) if name else None
    if path is None:
        return None
    # by absolute path from here: the scanners are shared with 1.x and would otherwise resolve
    # a bare name against the flat workflows/ folder, which 2.0 does not read
    cap = workflow_capacity(str(path), wdir)
    vscan = scan_video_workflow_file(str(path), wdir)
    iscan = scan_workflow_file(str(path), wdir)
    kind = cap.kind  # the folder decides, else the graph; see thm_core.assembler.workflow_capacity
    has = getattr(iscan, "has", None) or {}
    has_lora = bool(has.get("lora")) or bool(vscan.lora_high_titles or vscan.lora_low_titles or vscan.lora_single_titles)
    has_negative = bool(has.get("negative_prompt")) or bool(vscan.negative_titles)
    return WorkflowInfo(
        name=Path(name).name,
        kind=kind,
        image_slots=cap.image_slots,
        video_slots=cap.video_slots,
        supports_start_frame=cap.supports_start_frame,
        supports_end_frame=cap.supports_end_frame,
        addresses_by_token=cap.addresses_by_token,
        has_lora=has_lora,
        has_negative=has_negative,
        supports_draft=cap.supports_draft,
        supports_size=cap.supports_size,
        is_default=Path(name).name == (DEFAULT_VIDEO_WORKFLOW_FILENAME if kind == 'video' else DEFAULT_PROJECT_WORKFLOW_FILENAME),
        error=cap.error,
    )


def bin_detail(project: Project, bin_: Bin, env: Env, workflows_dir) -> BinDetail:
    resolved = resolve_references(project, bin_, isfile=env.isfile)
    cap = env.capacity(bin_.workflow)
    refs: list[ReferenceOut] = []
    for r in resolved:
        sub = reference_sub_label(cap, r.media_type, r.ordinal)
        refs.append(
            ReferenceOut(
                id=r.reference.id,
                handle=r.handle,
                media=r.media,
                bin_id=r.reference.bin if r.bin else None,
                bin_name=r.bin.name if r.bin else r.handle,
                ordinal=r.ordinal,
                sub_label=sub,
                active=sub != "unused",
                missing=not r.exists,
            )
        )
    held = newest_first([project.media_by_id[m] for m in bin_.media if m in project.media_by_id])
    selected = effective_selected(project, bin_)
    return BinDetail(
        bin=bin_,
        media=held,
        selected=selected.id if selected else None,
        references=refs,
        capacity=capacity_out(cap),
        workflow=workflow_info(bin_.workflow, workflows_dir) if bin_.workflow else None,
        kind=bin_kind(bin_, env),
        project_slug=project.slug,
        project_name=project.name,
    )
