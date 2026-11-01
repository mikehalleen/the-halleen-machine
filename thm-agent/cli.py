"""THM agent CLI: drive a project through the server's command API, one call per invocation.

    python thm-agent/cli.py wait                         # block until a brief is waiting
    python thm-agent/cli.py --project rail-dawn-promo connect --harness claude-code
    python thm-agent/cli.py --project rail-dawn-promo inbox
    python thm-agent/cli.py --project rail-dawn-promo cmd set_bin_field --payload '{"bin":"b_1","field":"length_s","value":8}'
    python thm-agent/cli.py --project rail-dawn-promo generate --bin b_1 --wait
    python thm-agent/cli.py --project rail-dawn-promo thumb m_1 --out take.webp

The inbox cursor and session id persist per project in ``thm-agent/.state/``
(agent-side, git-ignored) so a fresh process picks up where the last one left
off. ``--json`` prints the server's JSON for the harness to parse. Output is
ASCII only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from thm_client import PARAM_KEYS, ThmClient, ThmError, check_tokens, unknown_params  # noqa: E402

STATE_DIR = HERE / ".state"
DEFAULT_SERVER = "http://127.0.0.1:8010"
# the agent tool kit: one folder per tool with its own venv; see cmd_tools / cmd_tool
TOOLS_DIR = HERE.parent / "thm-tools"
MEDIA_ID_RE = re.compile(r"^m_[0-9a-f]{4,}$")

# Every command the server accepts and its payload, kept here so ``commands`` can answer offline
# and the skill's table has one source. tests/test_thm_client.py diffs it against
# thm_core.commands.COMMANDS, so a command added there without a line here fails the build.
# Every command also takes ``reason``, ``requested``, ``quiet`` and ``card``.
SETTABLE_FIELDS = ("name", "slug", "workflow", "length_s", "prompt", "negative", "loras", "selected", "description", "tags")

# What each subcommand says it is doing, sent as a header on every call so the app's strip can
# show the user the agent is on it. ``status`` sets a phrase of the agent's own for longer steps.
DOING = {
    "inbox": "reading your messages", "watch": "waiting for you", "project": "reading the project", "bin": "reading a bin",
    "history": "reading the take history", "recipe": "reading a recipe", "recall": "restoring a take's recipe", "check": "checking the prompt's handles",
    "guide": "reading the workflow's prompt guide", "generate": "queuing a render", "jobs": "checking renders",
    "thumb": "looking at a take", "frames": "looking at a clip", "post": "writing to you", "blocker": "writing to you",
    "propose-shots": "proposing shots",
    "enhance-requests": "picking up an enhance request", "enhance-reply": "answering an enhance request",
    "upload": "uploading a file", "gif": "making a GIF", "end": "wrapping up", "connect": "connecting",
    "file": "looking at what you sent", "keep": "putting your file in a bin", "seen": "clearing new markers",
    "notes": "working through your notes", "answer": "working through your notes", "resume": "catching up on the project", "rules": "reading the project rules",
    "sequence": "working on the sequence", "tools": "checking the tool kit", "tool": "measuring media with a tool", "create": "making the project",
    "publish": "drafting the post", "withdraw": "withdrawing a card", "cancel": "cancelling a render", "restart-comfy": "restarting ComfyUI",
}
DOING_CMD = {
    "generate": "queuing a render", "propose_shots": "proposing shots", "mark_take": "marking a take", "set_bin_field": "editing a bin", "create_bin": "making a bin",
    "bind_reference": "binding a reference", "rebind_reference": "binding a reference", "unbind_reference": "unbinding a reference",
    "start_from_media": "starting from a take",
    "post_message": "writing to you", "set_project_settings": "configuring the project", "delete_bin": "deleting a bin",
    "create_post": "drafting the post", "set_post_field": "writing the post", "delete_post": "dropping a post", "upload_post": "uploading to YouTube",
    "duplicate_bin": "cloning a bin",
    "create_sequence": "making a sequence", "add_sequence_items": "building the sequence", "reorder_sequence": "reordering the sequence",
    "remove_sequence_item": "editing the sequence", "set_item_trim": "trimming a shot in the sequence", "set_item_still": "editing the sequence",
    "set_item_agent": "editing the sequence", "set_sequence_agent": "editing the sequence", "set_sequence_field": "renaming the sequence", "clone_sequence": "cloning the sequence", "delete_sequence": "deleting a sequence",
    "set_sequence_layer": "laying sound under the sequence", "remove_sequence_layer": "editing the sequence's sound", "set_item_mix": "mixing a shot",
}
COMMAND_TABLE: dict[str, tuple[tuple[str, str], ...]] = {
    "create_bin": (("bin_name", "the title: a few words, never a number or prefix; default bin-n"), ("workflow", "default: the project's image workflow"), ("length_s", "video only"), ("tags", "list"), ("shot", "true for a shot of the narrative (numbered, @shot-N); false for an asset: a character, a plate, a reference clip")),
    "duplicate_bin": (("bin", ""), ("with_media", "copy the gallery too; default false"), ("shot", "the copy is a shot of its own, with its own number")),
    "set_bin_field": (("bin", ""), ("field", "one of " + " ".join(SETTABLE_FIELDS) + "; name is the title and rewrites nothing; slug is the handle and is the user's only"), ("value", "")),
    "bind_reference": (("bin", ""), ("media", ""), ("position", "0-based index among the bin's references (0 = first; a start frame is the first image reference, the end frame the second); omit to append; past the end appends")),
    "unbind_reference": (("bin", ""), ("reference", "")),
    "rebind_reference": (("bin", ""), ("reference", ""), ("media", "same handle, different picture")),
    "reorder_references": (("bin", ""), ("order", "every reference id")),
    "start_from_media": (("media", ""), ("bin", "an existing bin to bind into; omit to make one"), ("bin_name", "new bin only; the title; default bin-n"), ("workflow", "new bin, or a bin with none; default: the project's image workflow"), ("shot", "the new bin is a shot of the narrative (numbered)")),
    "recall_generation": (("bin", ""), ("from_media", ""), ("workflow", ""), ("prompt", ""), ("negative", ""), ("references", "[{media, handle}]"), ("loras", "")),
    "generate": (("bin", ""), ("params", "see params.md; a draft is {\"quarter\": true} on a video plus {\"draft\": true} where the workflow has a draft setting (`bin` shows `draft=supported`)"), ("hold", "queue it held: the user releases or discards it from the panel")),
    "propose_shots": (
        ("title", "the batch, in a few words"),
        ("items", "[{name | bin, shot (default true; false for an asset bin), description, prompt?, negative?, workflow?, references [media ids], needs [{bin | bin_name, what}] for a reference not generated yet, length_s?, quarter, draft, params, tags}]; name is the title, never numbered: the server numbers shots; the propose-shots verb reads them from a file"),
        ("hold", "never send it: the user's \"later\" on the card writes it, and the renders are saved held"),
    ),
    "mark_take": (("media", ""), ("status", "pass | fail | in_progress"), ("note", "what changed, what it fixed, what it broke, what is open")),
    "add_media_to_bin": (("bin", ""), ("media", "the arrival becomes selected")),
    "remove_media_from_bin": (("bin", ""), ("media", "the record stays")),
    "delete_media": (("media", "everywhere, cascading"),),
    "delete_bin": (("bin", "its media go too, unless another bin holds or references them"),),
    "set_project_settings": (("project_name", ""), ("brief", "the revised brief a plan session proposes"), ("width", ""), ("height", ""), ("image_workflow", ""), ("video_workflow", ""), ("video_length_s", ""), ("image_prompt", ""), ("video_prompt", ""), ("image_negative", ""), ("video_negative", ""), ("private", "true | false; no cover on the project list, and the agent cannot see the project"), ("youtube_channel", "the user's pick in the project settings, never yours (wrong_actor)")),
    "answer_notes": (("notes", "message ids of the user's notes"), ("did", "what you did about them, in a few words; shown on the note")),
    "withdraw_change": (("change", "one of your own cards still proposed or pending"), ("why", "a few words the user reads on the closed card")),
    "add_project_rule": (("kind", "qc | continuity | other"), ("text", "the user's answer, as a rule every later take is checked against")),
    "remove_project_rule": (("rule", "the rule id, from `rules`"),),
    "post_message": (("text", ""), ("attachments", "media ids"), ("files", "user only: ids of files uploaded for the note, not project media"), ("kind", "agent | blocker"), ("context", "user only: the bin/media the note is about")),
    "start_session": (("notes", ""), ("refs_bin", ""), ("mode", "auto | manual | image_review | video_review | plan; user only"), ("drafts", "true | false: drafts first; user only")),
    "set_session_mode": (("mode", "auto | manual | image_review | video_review | plan"), ("drafts", "true | false: the drafts-first switch, independent of the mode")),
    "connect_session": (("harness", ""), ("takeover", False), ("rejoin", False)),
    "end_session": (),
    "create_post": (("bin", "the bin whose current video the post is about"), ("platform", "youtube"), ("media", "another video in that bin; default the current one"), ("title", "up to 100 characters"), ("description", "up to 5000 bytes"), ("tags", "list, up to 500 characters together")),
    "set_post_field": (("post", ""), ("field", "title, description, tags, privacy, synthetic, made_for_kids, media, thumbnail, publish_at, extra"), ("value", "")),
    "delete_post": (("post", ""),),
    "upload_post": (("post", "a card (--propose) unless --requested; the video goes to the project's channel, private"),),
    "create_sequence": (("sequence_name", "default Sequence N; its exports bin is made at the first export"),),
    "set_sequence_field": (("sequence", ""), ("field", "name | output_bin"), ("value", "name: the title (the exports bin does not follow). output_bin: a bin id exports land in, or null for a new <name> Exports at the next export")),
    "clone_sequence": (("sequence", "items, order, trims, still lengths and agent settings; a new empty output bin"),),
    "delete_sequence": (("sequence", ""), ("delete_exports", "true takes the output bin and its media too; default false keeps it as an ordinary bin")),
    "add_sequence_items": (("sequence", ""), ("bins", "bin ids, in this order; each plays its selected take; a bin sits in a sequence once"), ("at", "0-based position to insert before; omit to append")),
    "remove_sequence_item": (("sequence", ""), ("bin", "the bin and its takes stay")),
    "reorder_sequence": (("sequence", ""), ("order", "every bin id in the sequence, in the new order")),
    "set_item_trim": (("sequence", ""), ("bin", "a video item"), ("head_s", "seconds dropped from the head"), ("tail_s", "seconds dropped from the tail; both 0 = the full take")),
    "set_item_still": (("sequence", ""), ("bin", "an image item"), ("still_s", "how long the picture shows; min 0.5")),
    "set_item_agent": (("sequence", ""), ("bin", ""), ("props", "your settings on ONE item the app has no control for (a mute, a pop removal, a dissolve out), merged; null removes a key; shown read-only to the user. Must include \"summary\": a few words about this item alone, times from its start (\"pop removal, 0.3s fade in\"); never \"whole sequence\" wording, that is set_sequence_agent's")),
    "set_sequence_agent": (("sequence", ""), ("props", "your settings that span the whole sequence (a walla bed, room tone, music), merged; null removes a key; shown once under the player. Must include \"summary\" (\"walla bed, 0.3s fade in at the start, 0.8s fade out at the end\")")),
    "set_sequence_layer": (("sequence", ""), ("layer", "{id (music, walla), media (audio, or a video with sound), source_in_s, start_frame (default 0), end: picture | source, gain_db, fade_in_s, fade_out_s}; the export lays it down in one piece under the picture, never cut and joined"), ("summary", "the user's line under the player, required: what is under the sequence now")),
    "remove_sequence_layer": (("sequence", ""), ("layer", "the layer id; every item's offset for it goes too"), ("summary", "what the user hears now; \"\" clears the line when nothing is left")),
    "set_item_mix": (("sequence", ""), ("bin", "the item"), ("clip_db", "this item's own audio, dB"), ("layers", "{layer id: dB offset on this item, or null = muted here}; merged per id"), ("edge_fade_ms", "fade on the item's own audio at both edges, default 10"), ("ramp_ms", "how fast a layer's gain changes at the item's edges, default 30"), ("reviewed_start_frame", "the item's start frame when you last QC'd it against the layers (sequence preview); a move since then shows as layer_moved"), ("summary", "the item's line for the user, required when the mix departs from the defaults")),
}


class UsageError(Exception):
    """A bad invocation: printed to stderr, exit code 2."""


# -- state ----------------------------------------------------------------------


def state_path(slug: str) -> Path:
    return STATE_DIR / f"{slug}.json"


def load_state(slug: str) -> dict:
    try:
        return json.loads(state_path(slug).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(slug: str, **fields) -> None:
    """Merge ``fields`` into the project's state file; passing None clears that field."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    data = load_state(slug)
    data.update(fields)
    state_path(slug).write_text(json.dumps(data, indent=2), encoding="utf-8")


# -- output ---------------------------------------------------------------------


def emit(args, data, human: str | None = None) -> None:
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=True))
    elif human is not None:
        print(human)
    elif data is not None:
        print(json.dumps(data, indent=2, ensure_ascii=True))


def describe_messages(messages: list[dict], slug: str | None = None) -> str:
    """One line per message. With ``slug`` every line names the project it came from: one agent
    drives many projects, and a note read under the wrong slug is acted on in the wrong one."""
    lines = []
    where = f"[{slug}] " if slug else ""
    for m in messages:
        kind = m.get("kind", "?")
        text = (m.get("text") or "").replace("\n", " ")
        tail = ""
        if m.get("open"):
            # read earlier and never dealt with: the id is what `answer` takes
            tail += f" [OPEN {m.get('id')}]"
        if m.get("attachments"):
            tail += f" [attachments: {', '.join(m['attachments'])}]"
        if m.get("files"):
            # the note's own files, in no bin: `file <id> --out` to look, `keep <id>` to make one media
            names = ", ".join("{} ({})".format(f.get("id"), f.get("name") or f.get("type")) for f in m["files"])
            tail += f" [files: {names}]"
        ctx = m.get("context")
        if ctx and (ctx.get("bin") or ctx.get("sequence")):
            # what the user was looking at: "this take" / "this shot" / "this cut" in the text means these ids
            parts = []
            if ctx.get("sequence"):
                seq = f"sequence {ctx['sequence']}"
                if ctx.get("sequence_name"):
                    seq += f' "{ctx["sequence_name"]}"'
                parts.append(seq)
            if ctx.get("bin"):
                b = (f"item #{ctx['position']} " if ctx.get("position") else "") + f"bin {ctx['bin']}"
                if ctx.get("bin_name"):
                    b += f' "{ctx["bin_name"]}"'
                parts.append(b)
            if ctx.get("media"):
                md = f"media {ctx['media']}"
                if ctx.get("media_label"):
                    md += f" ({ctx['media_label']})"
                parts.append(md)
            tail += f" [about: {' - '.join(parts)}]"
        if m.get("change"):
            tail += f" [change {m['change']}"
            if m.get("status"):
                tail += f" {m['status']}"
            if m.get("note"):
                tail += f": {m['note']}"
            tail += "]"
        lines.append(f"{where}{m.get('ts', '')} {kind:8} {text}{tail}")
    if any(m.get("open") for m in messages):
        lines.append(
            f"{where}these notes were read and never dealt with. Each is a task: do it (or decide you cannot, and why), then close it with "
            '`answer <id> [<id> ...] --did "what you did, in a few words"`. `notes` lists what is still open; watch does not block while any are.'
        )
    return "\n".join(lines) if lines else f"{where}(no messages)"


def note_ids(messages: list[dict]) -> list[str]:
    """The ids of the notes among ``messages``: each is open until ``answer`` names it."""
    return [m["id"] for m in messages if m.get("id") and (m.get("kind") == "user" or (m.get("kind") == "decision" and m.get("status") == "replied" and (m.get("note") or m.get("files"))))]


def warn(text: str) -> None:
    """Advisory lines go to stderr so ``--json`` stdout stays parseable."""
    print(f"warning: {text}", file=sys.stderr)


def describe_check(result: dict, references: list[dict]) -> str:
    lines = []
    for r in references:
        media = r.get("media") or {}
        lines.append(f"  @{r.get('handle')} ordinal={r.get('ordinal')} {r.get('sub_label') or '-'} media={media.get('id') or 'missing'} from={r.get('bin_name')}")
    for h in result["unknown"]:
        lines.append(f"  unknown @{h}: the prompt names it, nothing is bound to it")
    for h in result["unused"]:
        lines.append(f"  unused @{h}: bound, never mentioned in the prompt")
    if not result["unknown"] and not result["unused"]:
        lines.append(f"  ok: {len(result['used'])} handle(s) used, none unknown")
    return "\n".join(lines) if lines else "  (no references)"


def preflight(client, slug: str, bin_id: str, params: dict | None) -> dict | None:
    """Before a render: the handle check and the param-key check, as warnings on stderr. Returns
    the bin detail so the caller need not fetch it twice; None when the bin could not be read."""
    try:
        detail = client.bin(slug, bin_id)
    except ThmError:
        return None
    b = detail.get("bin") or {}
    cap = detail.get("capacity") or {}
    res = check_tokens(b.get("prompt", ""), b.get("negative", ""), detail.get("references") or [], bool(cap.get("addresses_by_token")))
    for h in res["unknown"]:
        warn(f"unknown_token @{h} matches no reference in this bin")
    for h in res["unused"]:
        warn(f"unused_handle @{h} appears nowhere in the prompt")
    kind = detail.get("kind")
    for k in unknown_params(kind, params):
        warn(f"unknown param {k!r}: the {kind or 'image'} runner never reads it (it reads {', '.join(PARAM_KEYS.get(kind or 'image', ()))})")
    return detail


def describe_project(detail: dict) -> str:
    sess = detail.get("agent_session") or {}
    lines = [f"{detail['name']} ({detail['slug']}) v{detail.get('version')}  pending: {detail.get('pending_changes', 0)}"]
    lines.append(f"brief: {detail.get('brief') or '(none)'}")
    d = detail.get("defaults") or {}
    if detail.get("configured"):
        prompts = sum(1 for k in ("image_prompt", "video_prompt", "image_negative", "video_negative") if (d.get(k) or "").strip())
        lines.append(
            f"settings: {d.get('width')}x{d.get('height')} image={d.get('image_workflow') or '-'} video={d.get('video_workflow') or '-'} "
            f"video_length_s={d.get('video_length_s')} global_prompts={prompts}"
        )
    else:
        lines.append("settings: not configured (set canvas and default workflows with set_project_settings)")
    if sess:
        lines.append(f"session {sess.get('id')} {sess.get('status')} mode={sess.get('mode') or '-'} harness={sess.get('harness') or '-'}")
    if detail.get("blocker"):
        lines.append(f"open blocker: {detail['blocker'].get('text', '')}")
    if detail.get("tags"):
        lines.append(f"tags: {', '.join(detail['tags'])}")
    for b in detail.get("bins", []):
        sel = (b.get("selected_media") or {}).get("id") or "-"
        tags = f" tags={','.join(b['tags'])}" if b.get("tags") else ""
        lines.append(
            f"  {b['id']}  {b['name']:14} {b.get('kind') or '-':5} workflow={b.get('workflow') or '-'} "
            f"media={len(b.get('media') or [])} takes={b.get('take_count', 0)} selected={sel}{tags}"
        )
    return "\n".join(lines)


def describe_result(result: dict) -> str:
    lines = []
    ch = result.get("change")
    if ch:
        dec = (ch.get("decision") or {}).get("status")
        lines.append(f"change {ch['id']}: {ch.get('description')}" + (f" [{dec}]" if dec else ""))
    gen = result.get("generation")
    if gen:
        params = gen.get("params") or {}
        tier = "draft" if (params.get("quarter") or params.get("draft")) else "full quality"
        lines.append(f"generation {gen['id']} {gen.get('status')} seed={params.get('seed')} ({tier})")
        if gen.get("status") in ("queued", "running"):
            lines.append(f"job {gen['id']}: `watch --jobs {gen['id']}` waits on it; `cancel {gen['id']}` stops it")
    for g in result.get("generations") or []:
        if gen is None or g.get("id") != gen.get("id"):
            lines.append(f"generation {g['id']} {g.get('status')} (job {g['id']})")
    for w in result.get("warnings") or []:
        lines.append(f"warning: {w.get('kind')} {w.get('detail')}")
    for m in result.get("messages") or []:
        lines.append(f"message {m.get('kind')}: {m.get('text')}")
    lines.append(f"project version {result.get('project_version')}")
    if result.get("mode"):
        lines.append(f"mode {result['mode']}" + (" (drafts first: a full-quality render asks on a card)" if result["mode"].endswith("+drafts") else ""))
    return "\n".join(lines)


# -- commands -------------------------------------------------------------------


def need_project(args) -> str:
    if not args.project:
        raise UsageError("--project <slug> is required for this command")
    return args.project


def cmd_projects(client, args):
    projects = client.projects()
    emit(args, projects, "\n".join(f"{p['slug']:24} {p['name']}  bins={p.get('bin_count', 0)}" for p in projects) or "(no projects)")


def cmd_create(client, args):
    settings = None
    if args.settings:
        try:
            settings = json.loads(args.settings)
        except ValueError as exc:
            raise UsageError(f"--settings is not JSON: {exc}") from None
    detail = client.create_project(args.name, brief=args.brief, settings=settings)
    human = f"created {detail['slug']}\n" + describe_project(detail)
    if args.audio:
        # the song the project is built from: into a Music bin tagged music, before any session
        up = client.upload(detail["slug"], args.audio, bin_name="Music", tags=["music"], note="the song the project is built from")
        media = up.get("media") or {}
        detail = {**detail, "audio": up}
        human += f"\nsong {media.get('id')} ({media.get('type')}, {media.get('duration_s') or 0:.1f}s) in {(up.get('bin') or {}).get('name')} ({(up.get('bin') or {}).get('id')}); next: `tool audio beats {media.get('id')} --fps N` and `energy`"
    emit(args, detail, human + "\nnext: the user starts a session in the app; run wait")


def cmd_workflows(client, args):
    if args.action == "runs":
        runs = client.comfy_runs(limit=args.limit)
        emit(args, runs, "\n".join(describe_run(r) for r in runs) or "(no ComfyUI runs of the user's in history: they run it once in ComfyUI first)")
        return
    if args.action == "import":
        if not args.name:
            raise UsageError("workflows import needs a NAME")
        tags = {}
        for t in args.tag:
            node, sep, tag = t.partition("=")
            if not sep or not node.strip() or not tag.strip():
                raise UsageError(f"--tag wants NODE=TAG, got {t!r}")
            tags[node.strip()] = tag.strip()
        system_prompt = None
        if args.system_prompt_file:
            with open(args.system_prompt_file, encoding="utf-8") as f:
                system_prompt = f.read()
        png = None
        if args.png:
            with open(args.png, "rb") as f:
                png = f.read()
        result = client.import_workflow(
            args.name, run=args.run, png=png, kind=args.kind, tags=tags, untag=args.untag, system_prompt=system_prompt,
            keep_builtin_enhancer=args.keep_builtin_enhancer, lora_marker=not args.no_lora_marker, dry_run=args.dry_run, replace=args.replace,
        )
        emit(args, result, describe_import(result))
        return
    wfs = client.workflows()
    emit(args, wfs, "\n".join(f"{w['kind']:5} {w['name']}  slots={w.get('image_slots', 0)}i/{w.get('video_slots', 0)}v negative={w.get('has_negative')}" for w in wfs) or "(no workflows)")


def describe_run(r: dict) -> str:
    models = ", ".join(r.get("models") or []) or "-"
    outs = ", ".join(r.get("outputs") or []) or "-"
    ui = "" if r.get("has_ui") else "  (no UI graph: API copy only)"
    return f"{r['id']}  {r.get('kind'):5} {r.get('status') or '?':8} nodes={r.get('nodes')}  models: {models}  outputs: {outs}{ui}"


def describe_import(r: dict) -> str:
    head = "DRY RUN (nothing written) " if r.get("dry_run") else ""
    lines = [f"{head}{r['name']} ({r['kind']}) from {r.get('source')}"]
    for k, v in (r.get("files") or {}).items():
        lines.append(f"  {k}: {v}")
    lines.append("tags:")
    for t in r.get("tags") or []:
        lines.append(f"  {t['node']:>6} {t['class']:28} {(t.get('from') or '-')[:30]:30} -> {t['to']}  ({t['why']})")
    for t in r.get("kept") or []:
        lines.append(f"  {t['node']:>6} {t['class']:28} kept {t['title']} (the user's own tag)")
    enh = r.get("enhancer") or {}
    src = enh.get("from") or {}
    mode = {
        "builtin_detached": f"built-in enhancer {src.get('class')} {src.get('llm')} taken out (removed {', '.join(enh.get('removed') or []) or '-'}); its system prompt kept",
        "builtin_kept": f"built-in enhancer {src.get('class')} {src.get('llm')} left in place; its system prompt copied",
        "supplied": "your system prompt",
        "existing": "the graph's own THM-EnhancerSystemPrompt",
    }.get(enh.get("mode"), enh.get("mode"))
    lines.append(f"enhancer: {mode} -> node {enh.get('node')}")
    lines.append("  " + (enh.get("system_prompt") or "").strip().replace("\n", "\n  ")[:1200])
    lora = r.get("lora") or {}
    lines.append(f"lora: marker {lora['marker']} after {lora.get('after')}" if lora.get("marker") else f"lora: no marker ({lora.get('why')})")
    lines.extend(f"baked: {b}" for b in r.get("baked") or [])
    lines.extend(f"note: {n}" for n in r.get("notes") or [])
    c = r.get("capabilities") or {}
    lines.append(
        f"scan: slots={c.get('image_slots')}i/{c.get('video_slots')}v start={c.get('supports_start_frame')} end={c.get('supports_end_frame')} "
        f"negative={c.get('has_negative')} lora={c.get('has_lora')} draft={c.get('supports_draft')} size={c.get('supports_size')} "
        f"controls={','.join(c.get('controls') or [])}" + (f" unknown={','.join(c['unknown_tags'])}" if c.get("unknown_tags") else "")
    )
    return "\n".join(lines)


def cmd_loras(client, args):
    files = client.loras()
    if args.find:
        files = [f for f in files if args.find.lower() in f["name"].lower()]
    emit(args, files, "\n".join(f["name"] for f in files) or "(no lora files)")


def cmd_wait(client, args):
    detail = client.wait_for_session(args.project, poll_s=args.poll, timeout_s=args.timeout)
    save_state(detail["slug"], session=(detail.get("agent_session") or {}).get("id"))
    emit(args, detail, describe_project(detail))


def cmd_connect(client, args):
    slug = need_project(args)
    result = client.connect(slug, args.harness)
    save_state(slug, session=client.session)
    # a connect is usually a resume (a crash, a reboot): start from the project's state, not memory
    summary = client.resume(slug)
    save_state(slug, mode=client.mode)
    emit(args, {**result, "resume": summary}, describe_result(result) + "\n" + describe_resume(summary, slug))


def describe_resume(r: dict, slug: str) -> str:
    lines = [f"RESUME {slug}: mode {r.get('mode')}"]
    notes = r.get("open_notes") or []
    lines.append(f"open notes: {len(notes)}" + (" -- `notes` lists them, each is a task" if notes else ""))
    for j in r.get("jobs") or []:
        lines.append(f"render {j['job']} in {j.get('bin_name') or j['bin']}: {j['status']}" + (f" ({j['task']})" if j.get("task") else ""))
    for g in r.get("interrupted") or []:
        lines.append(f"INTERRUPTED {g['generation']} in {g.get('bin_name') or g['bin']} at {g['ts']}: {g.get('why')} -- re-run it or let it go")
    last = r.get("last_agent_post")
    if last:
        lines.append(f"your last post ({last.get('ts')}): {(last.get('text') or '')[:200]}")
    for m in r.get("moved") or []:
        lines.append(f"MOVED {m['sequence_name']} #{m['position']} {m['name']}: reviewed at frame {m['reviewed_start_frame']}, now starts at {m['start_frame']} -- re-QC it against the layers (sequence preview)")
    rules = r.get("rules") or []
    lines.append(f"project rules: {len(rules)}" + ("" if not rules else ""))
    for rule in rules:
        lines.append(f"  {rule['id']} [{rule.get('kind')}] {rule.get('text')}")
    return "\n".join(lines)


def cmd_resume(client, args):
    slug = need_project(args)
    summary = client.resume(slug)
    emit(args, summary, describe_resume(summary, slug))


def cmd_rules(client, args):
    slug = need_project(args)
    rules = client.resume(slug).get("rules") or []
    emit(args, rules, "\n".join(f"{r['id']} [{r.get('kind')}] {r.get('text')}" for r in rules) or "(no project rules)")


def cmd_project(client, args):
    detail = client.project(need_project(args))
    emit(args, detail, describe_project(detail))


def cmd_bin(client, args):
    slug = need_project(args)
    detail = client.bin(slug, args.bin)
    b = detail["bin"]
    who = f"shot {b['shot']} \"{b['name']}\"" if b.get("shot") else b["name"]
    lines = [
        f"{who} ({b['id']}) handle=@{b.get('slug') or '-'} {detail.get('kind') or '-'} workflow={b.get('workflow') or '-'} length_s={b.get('length_s')}",
        f"prompt: {b.get('prompt', '')}",
        f"negative: {b.get('negative', '')}",
        f"tags: {', '.join(b.get('tags') or []) or '-'}",
        f"selected: {detail.get('selected') or '-'}",
    ]
    for m in detail.get("media", []):
        origin = m.get("origin") or {}
        take = f"take {m['take']}" if m.get("take") else origin.get("type", "media")
        line = f"  media {m['id']} {m.get('type')} {take}"
        if origin.get("generation"):
            line += f" gen={origin['generation']}"
        if origin.get("source"):
            line += f" from={origin['source']}"
        if origin.get("note"):
            line += f" ({origin['note']})"
        if m.get("id") == detail.get("selected"):
            line += " *"
        if m.get("qc"):
            line += f" qc={m['qc']['status']}"
            if m["qc"].get("note"):
                line += f": {m['qc']['note']}"
        lines.append(line)
    for r in detail.get("references", []):
        media = r.get("media") or {}
        lines.append(f"  ref {r['id']} @{r['handle']} media={media.get('id') or 'missing'} from={r.get('bin_name')} {r.get('sub_label') or ''}".rstrip())
    cap = detail.get("capacity") or {}
    lines.append(f"capacity: image_slots={cap.get('image_slots')} video_slots={cap.get('video_slots')} start={cap.get('supports_start_frame')} end={cap.get('supports_end_frame')} draft={'supported' if cap.get('supports_draft') else 'no'} quarter={'supported' if cap.get('supports_size') else 'no'}")
    for j in detail.get("active_jobs") or []:
        lines.append(f"active job {j['job']} {j['status']} {j.get('progress')}% {j.get('task') or ''}")
    if detail["bin"].get("unseen"):
        lines.append(f"unseen: {' '.join(detail['bin']['unseen'])}")
    emit(args, detail, "\n".join(lines))


def cmd_inbox(client, args):
    slug = need_project(args)
    before = load_state(slug).get("mode")
    messages, cursor = client.inbox(slug)
    save_state(slug, cursor=cursor)
    human = f"project {slug}\n" + describe_messages(messages, slug) + f"\ncursor {cursor}" + (f"\nmode {client.mode}" if client.mode else "")
    if client.mode and before and client.mode != before:
        # the switch is easy to miss as one system line among many: say it on its own
        human += f"\nMODE CHANGED: {before} -> {client.mode}. Re-plan for {client.mode} before the next step"
        if client.mode.endswith("+drafts") and not before.endswith("+drafts"):
            human += " (drafts first now: every render goes at draft settings; a full-quality one asks on a card)"
    ids_ = note_ids(messages)
    if ids_:
        # the count is the point: a round of notes read in one go is that many tasks, not one
        human += (
            f"\n{len(ids_)} note(s) here, each a task of its own: {' '.join(ids_)}"
            '\nwork through ALL of them, oldest first; close each with `answer <id> --did "..."`. `notes` shows what is still open.'
        )
    emit(args, {"project": slug, "messages": messages, "cursor": cursor, "notes": ids_, "mode": client.mode}, human)


def cmd_notes(client, args):
    slug = need_project(args)
    owed = [{**m, "open": True} for m in client.open_notes(slug)]
    if getattr(client, "notes_untracked", False):
        # never read this as "nothing open": the server cannot tell, so the feed is the only record
        raise UsageError(
            "this server is running code from before notes were tracked (no /notes route), so it cannot list what is open. "
            "Tell the user the server needs a restart; until then re-read the recent user notes in the feed yourself and act on every one."
        )
    emit(args, {"project": slug, "notes": owed}, describe_messages(owed, slug) if owed else f"[{slug}] no open notes: everything the user said is accounted for")


def cmd_answer(client, args):
    slug = need_project(args)
    refuse_local_paths(args.did)
    client.answer_notes(slug, args.notes, args.did)
    left = client.open_notes(slug)
    emit(args, {"project": slug, "answered": args.notes, "open": [m["id"] for m in left]}, f"closed {len(args.notes)} note(s); {len(left)} still open" + (": " + " ".join(m["id"] for m in left) if left else ""))


def cmd_withdraw(client, args):
    slug = need_project(args)
    if args.reason:
        refuse_local_paths(args.reason)
    result = client.withdraw(slug, args.change, args.reason or "")
    emit(args, result, describe_result(result))


def _job_line(j: dict) -> str:
    flags = " STALLED" if j.get("stalled") else ""
    flags += " (queued again)" if j.get("retried") else ""
    return f"{j['job']} {j.get('bin_name') or j['bin']} {j['status']:8} {j.get('progress') or 0}%{flags} {j.get('task') or ''} takes={j.get('takes')} {j.get('error') or ''}".rstrip()


def cmd_cancel(client, args):
    slug = need_project(args)
    job = client.cancel_job(slug, args.job, requested=args.requested, reason=args.reason or "")
    emit(args, job, f"cancelled: {_job_line(job)}")


def cmd_restart_comfy(client, args):
    slug = need_project(args)
    out = client.restart_comfy(slug, requested=args.requested, reason=args.reason or "")
    n = len(out.get("requeued") or [])
    emit(args, out, f"ComfyUI restarting via {out.get('script')}; {n} live render(s) queued again: {' '.join(out.get('requeued') or []) or '-'}\nnext: watch --project {slug}" + (f" --jobs {','.join(out['requeued'])}" if n else ""))


QUIET = (
    "quiet: nothing from the user yet. That is normal, not a problem: they are multitasking and you are the one waiting. "
    "Run watch again at once, say nothing, and keep doing so for as long as it takes -- hours is fine. The only thing that ends a wait is the user."
)


def cmd_watch(client, args):
    """Blocks until something needs the agent. Returns empty every ``--timeout`` seconds (under
    the harness's own command limit) so the wait is never killed from outside and never reads
    as an error: a quiet stretch is the user's time, and the agent simply watches again."""
    if not args.timeout:
        args.timeout = None  # 0: wait forever
    try:
        _watch(client, args)
    except TimeoutError:
        emit(args, {"project": args.project, "messages": [], "why": "quiet", "next": "watch"}, QUIET)


def _jobs_arg(args) -> tuple[str, ...]:
    return tuple(j.strip() for j in (getattr(args, "jobs", None) or "").split(",") if j.strip())


def _watch(client, args):
    if args.project:
        slug = args.project
        st = load_state(slug)
        messages = client.wait_for_inbox(slug, cursor=st.get("cursor"), jobs=_jobs_arg(args), mode=st.get("mode"), poll_s=args.poll, timeout_s=args.timeout)
        emit(args, {"project": slug, "messages": messages}, f"project {slug}\n" + describe_messages(messages, slug) + f"\nnext: --project {slug} inbox to read these and move the cursor")
        return
    # no project: every project is yours; whichever needs you first wakes you
    cursors, modes, sessions = {}, {}, {}
    if STATE_DIR.is_dir():
        for path in STATE_DIR.glob("*.json"):
            st = load_state(path.stem)
            cursors[path.stem] = st.get("cursor")
            modes[path.stem] = st.get("mode")
            sessions[path.stem] = st.get("session")
    woke = client.wait_for_any(cursors, jobs=_jobs_arg(args), modes=modes, sessions=sessions, poll_s=args.poll, timeout_s=args.timeout)
    human = f"project {woke['slug']}: {woke['why']}\n" + describe_messages(woke["messages"], woke["slug"])
    tail = "\nnext: connect" if woke["why"] in ("session waiting", "agent gone") else f"\nnext: --project {woke['slug']} inbox"
    if woke["why"] == "agent gone" and woke.get("session") and woke.get("session") == load_state(woke["slug"]).get("session"):
        # its own session: the server lost track of it (a restart, a quiet spell), nobody took it
        human = (
            f"project {woke['slug']}: YOUR session {woke['session']} reads as unattended -- the server restarted or has not "
            "heard from you for a while; nobody else has it"
        )
        tail = f"\nnext: --project {woke['slug']} connect --harness claude-code (you get it straight back)"
    emit(args, woke, human + tail)


def cmd_sessions(client, args):
    rows = client.sessions()
    lines = []
    for r in rows:
        line = f"{r['slug']:24} {r['status']:8} mode={r.get('mode') or '-':7} harness={r.get('harness') or '-':12} pending={r['pending_changes']}"
        if r.get("agent_gone"):
            line += "  AGENT GONE: connect to take it over"
        if r.get("blocker"):
            line += f"  BLOCKER: {r['blocker']}"
        lines.append(line)
    emit(args, rows, "\n".join(lines) or "(no projects)")


def cmd_mode(client, args):
    slug = need_project(args)
    if args.mode is None and args.drafts is None:
        raise UsageError("name a mode, --drafts on|off, or both")
    result = client.set_mode(slug, args.mode, drafts=None if args.drafts is None else args.drafts == "on", requested=args.requested)
    emit(args, result, describe_result(result))


def cmd_history(client, args):
    slug = need_project(args)
    rows = client.history(slug, args.bin, limit=args.limit)
    lines = []
    for r in rows:
        take = f"take {r['take']}" if r.get("take") else r["media_state"]
        params = f"seed={r.get('seed')}"
        if r.get("length_s") is not None:
            params += f" length_s={r['length_s']}"
        if r.get("quarter"):
            params += " quarter"
        if r.get("draft"):
            params += " draft"
        qc = r.get("qc") or {}
        head = f"{take:10} {r['generation']} {r.get('status') or '-':9} {params}  qc={qc.get('status') or '-'}"
        if r.get("recovered"):
            head += "  (recovered from file)"
        lines.append(head)
        if qc.get("note"):
            lines.append(f"    note: {qc['note']}")
        for w in r.get("warnings") or []:
            lines.append(f"    warning: {w.get('kind')} {w.get('detail')}")
        if r.get("prompt_diff"):
            lines.append("    " + r["prompt_diff"].replace("\n", "\n    "))
        elif r is rows[-1]:
            lines.append("    prompt: " + (r.get("prompt_typed") or "").replace("\n", " ")[:400])
        else:
            lines.append("    prompt: unchanged")
    emit(args, rows, "\n".join(lines) if lines else "(no generations in this bin)")


def cmd_recipe(client, args):
    slug = need_project(args)
    rec = client.recipe(slug, args.media)
    if not rec.get("found"):
        emit(args, rec, f"no recipe: {args.media} was not generated (an upload, clip or still)")
        return
    lines = [
        f"generation {rec.get('generation')} {rec.get('status') or ''} bin={rec.get('bin')} workflow={rec.get('workflow')}" + (" (workflow missing)" if rec.get("workflow_missing") else "") + (" (recovered from file)" if rec.get("recovered") else ""),
        f"params: {json.dumps(rec.get('params') or {}, ensure_ascii=True)}",
        f"prompt: {rec.get('prompt', '')}",
        f"negative: {rec.get('negative', '')}",
    ]
    for media, handle in zip(rec.get("references") or [], rec.get("handles") or []):
        lines.append(f"  ref @{handle} media={media}")
    for media in rec.get("missing") or []:
        lines.append(f"  missing reference media={media}")
    if rec.get("loras"):
        lines.append(f"loras: {', '.join(f'{l.get('name')}:{l.get('strength')}' for l in rec['loras'])}")
    for w in rec.get("warnings") or []:
        lines.append(f"warning: {w.get('kind')} {w.get('detail')}")
    emit(args, rec, "\n".join(lines))


def cmd_recall(client, args):
    """Back to the take: the recipe in the media's record goes onto the bin, and with
    --generate renders once more on the same seed and length."""
    slug = need_project(args)
    result = client.recall(slug, args.media, bin=args.bin, reason=args.reason)
    rec = result["recipe"]
    bin_id = args.bin or rec.get("bin")
    lines = [f"recalled {args.media} into bin {bin_id}: workflow={rec.get('workflow')} refs={len(rec.get('references') or [])} loras={len(rec.get('loras') or [])}"]
    lines.append(f"prompt: {rec.get('prompt', '')}")
    if rec.get("negative"):
        lines.append(f"negative: {rec['negative']}")
    for media, handle in zip(rec.get("references") or [], rec.get("handles") or []):
        lines.append(f"  ref @{handle} media={media}")
    for media in rec.get("missing") or []:
        lines.append(f"  missing reference media={media} (deleted; bind another before rendering)")
    ch = result.get("change")
    if ch is None:
        lines.append("(the bin already carried this recipe)")
    if rec.get("length_s") is not None:
        client.command(slug, "set_bin_field", {"bin": bin_id, "field": "length_s", "value": rec["length_s"], "quiet": True})
        lines.append(f"length_s {rec['length_s']} restored")
    gen = None
    detail = client.bin(slug, bin_id)
    for r in detail.get("references") or []:
        media = r.get("media") or {}
        lines.append(f"  ref {r['id']} @{r['handle']} media={media.get('id') or 'missing'}  (ids after the recall: rebind/unbind by these)")
    if args.generate:
        params = {k: v for k, v in (rec.get("params") or {}).items() if k in ("seed", "quarter", "draft")}
        if rec.get("seed") is not None:
            params["seed"] = rec["seed"]
        if (client.mode or "").endswith("+drafts"):
            # drafts first: the old take's tier is not the session's; every cheap setting the
            # workflow has goes on (a full-quality re-render is a card, never a bare recall)
            cap = detail.get("capacity") or {}
            if cap.get("supports_size") and detail.get("kind") == "video":
                params["quarter"] = True
            if cap.get("supports_draft"):
                params["draft"] = True
        reason = args.reason or f"the recipe of {args.media} again, same seed"
        res = client.generate(slug, bin_id, params=params or None, reason=reason)
        gen = res.get("generation")
        result = {**result, "generate": res}
        lines.append(f"queued generation {gen['id'] if gen else '?'} (job {gen['id'] if gen else '?'}) with params {json.dumps(params, ensure_ascii=True)}")
    else:
        seed = rec.get("seed")
        lines.append(f"next: change the one thing, then generate --bin {bin_id}" + (f" --params '{{\"seed\": {seed}}}' to hold the seed" if seed is not None else ""))
    emit(args, result, "\n".join(lines))


def cmd_check(client, args):
    slug = need_project(args)
    detail = client.bin(slug, args.bin)
    b = detail.get("bin") or {}
    cap = detail.get("capacity") or {}
    res = check_tokens(b.get("prompt", ""), b.get("negative", ""), detail.get("references") or [], bool(cap.get("addresses_by_token")))
    emit(args, {"bin": args.bin, **res, "references": detail.get("references") or []}, f"{b.get('name')} ({args.bin})\n" + describe_check(res, detail.get("references") or []))


def cmd_frames(client, args):
    slug = need_project(args)
    data = client.frames(slug, args.media, n=args.n, size=args.size)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    emit(args, {"media": args.media, "n": args.n, "size": args.size, "out": str(out), "bytes": len(data)}, f"wrote {len(data)} bytes to {out} ({args.n} frames, times stamped)")


def cmd_guide(client, args):
    info = client.workflow(args.workflow)
    head = (
        f"{info['name']} ({info['kind']}) slots={info.get('image_slots', 0)}i/{info.get('video_slots', 0)}v negative={info.get('has_negative')} "
        f"lora={info.get('has_lora')} start={info.get('supports_start_frame')} end={info.get('supports_end_frame')} tokens={info.get('addresses_by_token')} draft={'supported' if info.get('supports_draft') else 'no'} quarter={'supported' if info.get('supports_size') else 'no'}"
    )
    emit(args, info, f"{head}\n-- prompt guide ({info.get('guide_source') or 'none'}) --\n{info.get('guide') or '(none)'}")


def cmd_commands(client, args):
    if args.name:
        if args.name not in COMMAND_TABLE:
            raise UsageError(f"unknown command {args.name}; run commands for the list")
        fields = COMMAND_TABLE[args.name]
        human = f"{args.name}\n" + "\n".join(f"  {k}{('  ' + note) if note else ''}" for k, note in fields) + "\n  (+ reason, requested, quiet, card)"
        emit(args, {"name": args.name, "fields": [k for k, _ in fields]}, human)
        return
    lines = [f"{name:24} {', '.join(k for k, _ in fields) or '-'}" for name, fields in COMMAND_TABLE.items()]
    emit(args, {name: [k for k, _ in fields] for name, fields in COMMAND_TABLE.items()}, "\n".join(lines) + "\nevery command also takes reason, requested, quiet, card")


def cmd_upload(client, args):
    slug = need_project(args)
    result = client.upload(slug, args.file, bin=args.bin, bin_name=args.bin_name, source=args.source, note=args.note)
    media = result.get("media") or {}
    human = f"uploaded {media.get('id')} ({media.get('type')}) into {(result.get('bin') or {}).get('name')} ({(result.get('bin') or {}).get('id')})"
    if result.get("change"):
        human += "\n" + describe_result(result)
    emit(args, result, human)


def cmd_gif(client, args):
    slug = need_project(args)
    result = client.gif(slug, args.media, bin=args.bin, bin_name=args.bin_name, start_s=args.start, end_s=args.end, fps=args.fps, width=args.width)
    media = result.get("media") or {}
    b = result.get("bin") or {}
    human = f"made GIF {media.get('id')} ({media.get('width')}x{media.get('height')}) from {args.media} into {b.get('name')} ({b.get('id')})"
    if result.get("change"):
        human += "\n" + describe_result(result)
    emit(args, result, human)


def cmd_file(client, args):
    slug = need_project(args)
    # a note's file (f_...) or a take (m_...): one verb fetches either
    data = client.media_file(slug, args.file) if MEDIA_ID_RE.match(args.file) else client.message_file(slug, args.file)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    emit(args, {"file": args.file, "out": str(out), "bytes": len(data)}, f"wrote {len(data)} bytes to {out}")


def cmd_seen(client, args):
    slug = need_project(args)
    if not args.all and not args.media:
        raise UsageError("name the media to clear, or pass --all")
    result = client.post(f"/api/projects/{slug}/media/seen", {"media": list(args.media or []), "all": bool(args.all)})
    cleared = result.get("cleared") or []
    emit(args, result, f"cleared the new marker on {len(cleared)} media: {' '.join(cleared)}" if cleared else "nothing was marked new")


def cmd_keep(client, args):
    slug = need_project(args)
    result = client.keep_file(slug, args.file, bin=args.bin, bin_name=args.bin_name)
    media = result.get("media") or {}
    human = f"kept {args.file} as {media.get('id')} ({media.get('type')}) in {(result.get('bin') or {}).get('name')} ({(result.get('bin') or {}).get('id')})"
    emit(args, result, human)


# a path on the agent's disk: C:\\x\\y.mp4, /tmp/x.mp4, ~/x.png
_LOCAL_PATH_RE = re.compile(r"(?:\b[A-Za-z]:[\\/][^\s\"']+|(?:^|[\s(\"'])(?:~|/tmp|/home|/Users|/var|/mnt)/[^\s\"']+)")


def refuse_local_paths(text: str) -> None:
    """The user is in the app: a path on this machine's disk is not something they can open
    there, and a result that exists only as a path has not been delivered."""
    m = _LOCAL_PATH_RE.search(text or "")
    if m:
        raise UsageError(
            f"that text carries a local path ({m.group(0).strip()[:60]}). The user reads this in the app and cannot open a path: "
            "upload the file into a bin (`upload <file> --bin <id> | --bin-name \"Assemblies\" --note \"...\"`), then `post \"...\" --attach <media id>`."
        )


def cmd_post(client, args, kind="agent"):
    slug = need_project(args)
    refuse_local_paths(args.text)
    result = client.post_message(slug, args.text, kind=kind, attachments=args.attach or None)
    emit(args, result, describe_result(result))


def cmd_blocker(client, args):
    cmd_post(client, args, kind="blocker")


def cmd_enhance_requests(client, args):
    slug = need_project(args)
    reqs = client.enhance_requests(slug)
    human = []
    for r in reqs:
        # listing them here means you are about to write: the user's button reads "rewriting"
        try:
            client.enhance_ack(slug, r["id"])
        except ThmError:
            pass
        head = f"== {r['id']} ({r['kind']}, workflow {r.get('workflow') or 'default'})"
        human.append(f"{head}\n-- system prompt --\n{r['system_prompt']}\n-- message --\n{r['message']}\n")
    emit(args, {"requests": reqs}, "\n".join(human) if human else "(no pending enhance requests)")


def cmd_status(client, args):
    slug = need_project(args)
    result = client.set_activity(slug, args.text)
    emit(args, result, f"status: {result.get('doing')}")


def cmd_enhance_reply(client, args):
    slug = need_project(args)
    text = args.text or ""
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    result = client.enhance_reply(slug, args.id, prompt=text, error=args.error or "")
    emit(args, result, f"answered {args.id}")


def cmd_cmd(client, args):
    slug = need_project(args)
    try:
        payload = json.loads(args.payload) if args.payload else {}
    except ValueError as exc:
        raise UsageError(f"--payload is not JSON: {exc}") from None
    if not isinstance(payload, dict):
        raise UsageError("--payload must be a JSON object")
    if args.requested:
        payload["requested"] = True
    if args.quiet:
        payload["quiet"] = True
    if args.card:
        payload["card"] = True
    if args.reason and not payload.get("reason"):
        payload["reason"] = args.reason
    if args.name == "generate":
        preflight(client, slug, str(payload.get("bin") or ""), payload.get("params"))
    result = client.command(slug, args.name, payload, propose=args.propose, cursor=client.cursor)
    emit(args, result, describe_result(result))


SHOT_KEYS = ("name", "bin", "description", "prompt", "negative", "workflow", "references", "length_s", "quarter", "draft", "params", "tags")


def read_shots(args) -> tuple[str | None, list[dict]]:
    """The batch from ``--file`` (``-`` for stdin) or ``--items``: a JSON list of items, or an
    object ``{"title": ..., "items": [...]}``. Only the item keys the server knows pass through."""
    if args.file and args.items:
        raise UsageError("give --file or --items, not both")
    if args.file:
        text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    elif args.items:
        text = args.items
    else:
        raise UsageError("propose-shots needs --file <shots.json> (or - for stdin), --items '<json>', or --show <change id>")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise UsageError(f"shots are not JSON: {exc}") from None
    title = args.title
    if isinstance(data, dict):
        title = title or data.get("title")
        data = data.get("items")
    if not isinstance(data, list) or not data:
        raise UsageError("shots must be a non-empty JSON list of items, or {title, items}")
    items = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise UsageError(f"shot {i + 1} is not an object")
        unknown = sorted(set(item) - set(SHOT_KEYS))
        if unknown:
            raise UsageError(f"shot {i + 1} has unknown key(s) {', '.join(unknown)}; the keys are {', '.join(SHOT_KEYS)}")
        if not item.get("description"):
            raise UsageError(f"shot {i + 1} needs a description (the one-liner the card shows)")
        if not item.get("name") and not item.get("bin"):
            raise UsageError(f"shot {i + 1} needs a name (a new bin) or a bin (an existing one)")
        items.append(dict(item))
    return title, items


def describe_shots(change: dict) -> str:
    """The card's outcome, one line per item, as the agent reads it after the decision."""
    dec = change.get("decision") or {}
    payload = change.get("payload") or {}
    items = payload.get("items") or []
    applied = any(i.get("bin_id") for i in items if isinstance(i, dict))
    # a batch the user asked for out loud carries no decision: it landed done
    status = dec.get("status") or ("done" if applied else "proposed")
    lines = [f"change {change.get('id')}: {change.get('description')} [{status}]"]
    if dec.get("note"):
        lines.append(f"note: {dec['note']}")
    if status == "proposed":
        lines.append(f"{len(items)} shot(s) on the card; watch for the decision, then propose-shots --show {change.get('id')}")
        return "\n".join(lines)
    for i, item in enumerate(items):
        name = item.get("name") or item.get("bin") or f"shot {i + 1}"
        if status == "replied":
            lines.append(f"  {i + 1}. {name}: replied, nothing made -- act on the note (a revised card, or what it asks)")
            continue
        if status not in ("approved", "done"):
            lines.append(f"  {i + 1}. {name}: {status}, nothing made")
            continue
        if item.get("dropped"):
            lines.append(f"  {i + 1}. {name}: dropped")
            continue
        where = f"bin {item.get('bin_id') or '?'}"
        if payload.get("hold") and not item.get("error") and item.get("change"):
            # the user answered "later": approved, set up, and saved; they release it themselves
            lines.append(f"  {i + 1}. {name}: later -- {where}, render {item['change']} saved held; the user releases it, do not wait or re-ask")
        elif item.get("error"):
            lines.append(f"  {i + 1}. {name}: {where}, render refused: {item['error']}")
        elif item.get("edited") and item.get("prompt") is not None:
            lines.append(f"  {i + 1}. {name}: edited to \"{item.get('description')}\" -- {where}, no render: rewrite the prompt and generate")
        elif item.get("edited"):
            lines.append(f"  {i + 1}. {name}: edited to \"{item.get('description')}\" -- {where}, render {item.get('change') or '?'}")
        else:
            lines.append(f"  {i + 1}. {name}: kept -- {where}, render {item.get('change') or '?'}")
    return "\n".join(lines)


def cmd_propose_shots(client, args):
    slug = need_project(args)
    if args.show:
        change = client.change(slug, args.show)
        emit(args, change, describe_shots(change))
        return
    title, items = read_shots(args)
    result = client.propose_shots(slug, items, title=title, reason=args.reason, requested=args.requested)
    # one card per shot: the first is ``change``, all of them are in ``changes``
    seen: set = set()
    cards = [
        c for c in (result.get("change"), *(result.get("changes") or []))
        if c and c.get("command") == "propose_shots" and c.get("id") not in seen and not seen.add(c.get("id"))
    ]
    if args.requested:
        emit(args, result, "\n".join(describe_shots(c) for c in cards) + "\n" + describe_result({**result, "change": None, "changes": []}))
        return
    if args.wait and cards:
        decided = [client.wait_change(slug, c["id"], poll_s=args.poll, timeout_s=args.timeout) for c in cards]
        emit(args, {**result, "change": decided[0], "changes": decided}, "\n".join(describe_shots(c) for c in decided))
        return
    n = len(cards)
    card_ids = ", ".join(c.get("id") or "?" for c in cards)
    emit(args, result, f"proposed {n} shot{'s' if n != 1 else ''} as {n} card{'s' if n != 1 else ''} ({card_ids}); watch for the decisions, then propose-shots --show <card>")


def cmd_generate(client, args):
    slug = need_project(args)
    try:
        params = json.loads(args.params) if args.params else None
    except ValueError as exc:
        raise UsageError(f"--params is not JSON: {exc}") from None
    preflight(client, slug, args.bin, params)
    result = client.generate(slug, args.bin, propose=args.propose, params=params, requested=args.requested, card=args.card, reason=args.reason)
    gen = result.get("generation")
    if args.wait and gen and gen.get("status") != "held":
        # the same wait as watch: the render finishing, or a note / mode change first
        try:
            woke = client.wait_for_inbox(slug, cursor=client.cursor, jobs=(gen["id"],), mode=client.mode, poll_s=args.poll, timeout_s=args.timeout or 540.0)
        except TimeoutError:
            woke = []
        done = [w for w in woke if w.get("kind") == "job" and w.get("id") == gen["id"]]
        if done:
            gen = client.generation(slug, gen["id"])
            result = {**result, "generation": gen}
            human = describe_result(result) + f"\noutputs: {gen.get('outputs')}"
        else:
            reason = "something needs you" if woke else "the wait timed out"
            human = describe_result(result) + f"\nRETURNED EARLY ({reason}); render {gen['id']} keeps going -- `watch --project {slug} --jobs {gen['id']}` to wait on it again"
            if woke:
                human += "\n" + describe_messages(woke, slug)
            result = {**result, "woke": woke}
    else:
        human = describe_result(result)
    emit(args, result, human)


def _seq_line(item: dict) -> str:
    """One row of ``sequence show``: position, the bin, what plays, the length, trims and agent settings."""
    who = f"shot {item['shot']} \"{item['name']}\"" if item.get("shot") is not None else f"\"{item['name']}\""
    kind = item.get("kind")
    media = item.get("media") or {}
    if kind == "title":
        plays = "no take: title card"
    elif kind == "still":
        plays = f"still {media.get('id', '')}".rstrip()
    else:
        plays = f"take {media['take']}" if media.get("take") else f"clip {media.get('id', '')}"
    extras = []
    if item.get("head_s") or item.get("tail_s"):
        extras.append(f"[head {item.get('head_s', 0):g} tail {item.get('tail_s', 0):g}{' DROPPED: take length changed' if item.get('trim_dropped') else ''}]")
    if item.get("agent"):
        agent = dict(item["agent"])
        summary = agent.pop("summary", None)
        head = f"export only: {summary}" if summary else "export only"
        extras.append(f"[{head}" + ("; " + ", ".join(f"{k}={v}" for k, v in agent.items()) if agent else "") + "]")
    if item.get("mix"):
        mix = item["mix"]
        parts = []
        if mix.get("clip_db"):
            parts.append(f"clip {mix['clip_db']:+g} dB")
        for k, v in (mix.get("layers") or {}).items():
            parts.append(f"{k} muted" if v is None else f"{k} {float(v):+g} dB")
        if parts:
            extras.append("[mix: " + ", ".join(parts) + "]")
    if item.get("layer_moved"):
        extras.append(f"[MOVED since your review at frame {(item.get('mix') or {}).get('reviewed_start_frame')}: listen again]")
    frames = f"f{item.get('start_frame', 0)}-{item.get('start_frame', 0) + item.get('frames', 0)}"
    return f"#{item['position']:<3} {who:34} {item['bin']}  {plays:14} {item['duration_s']:.1f}s {frames:12} " + " ".join(extras)


def _change_flags(args, payload: dict) -> dict:
    if getattr(args, "quiet", False):
        payload["quiet"] = True
    if getattr(args, "card", False):
        payload["card"] = True
    if getattr(args, "requested", False):
        payload["requested"] = True
    if getattr(args, "reason", None):
        payload["reason"] = args.reason
    return payload


def _out_bin(seq: dict) -> str:
    return f"{seq['output_bin_name']} ({seq['output_bin']})" if seq.get("output_bin") else "a new <name> Exports bin, made at the first export"


def cmd_sequence(client, args):
    """The sequence verbs: the ordered list of bins the app plays and exports."""
    slug = need_project(args)
    action = args.seq_action
    if action is None or action == "list":
        seqs = client.sequences(slug)
        lines = [f"{s['id']}  {s['name']:24} {len(s['items'])} items  {s['total_s']:.1f}s  exports -> {_out_bin(s)}" for s in seqs]
        emit(args, seqs, "\n".join(lines) or "(no sequences; `sequence new` makes one)")
        return
    if action == "show":
        seq = client.sequence(slug, args.sequence)
        head = f"{seq['name']} ({seq['id']})  {len(seq['items'])} items  {seq['total_s']:.1f}s  {seq['without_take']} without a take  exports -> {_out_bin(seq)}"
        last = seq.get("last_export")
        tail = f"\nlast export: take {last.get('take')} {last.get('id')} {last.get('created')}" if last else "\nlast export: none yet"
        head += f"  {seq.get('fps', 24):g} fps, {seq.get('total_frames', 0)} frames"
        layer = dict(seq.get("agent") or {})
        if layer:
            summary = layer.pop("summary", None)
            head += f"\nexport only, whole sequence: {summary or ''}" + ("; " + ", ".join(f"{k}={v}" for k, v in layer.items()) if layer else "")
        for lay in seq.get("layers") or []:
            head += (f"\nlayer {lay['id']}: {lay['media']} from {lay.get('source_in_s', 0):g}s at frame {lay.get('start_frame', 0)}, to the end of the {lay.get('end', 'picture')}"
                     f", {lay.get('gain_db', 0):+g} dB, fade {lay.get('fade_in_s', 0):g}s/{lay.get('fade_out_s', 0):g}s")
        moved = [i for i in seq["items"] if i.get("layer_moved")]
        if moved:
            tail += "\nmoved since review (re-QC against the layers with `sequence preview`): " + ", ".join(f"#{i['position']} {i['name']}" for i in moved)
        emit(args, seq, head + "\n" + "\n".join(_seq_line(i) for i in seq["items"]) + tail)
        return
    if action == "export":
        status = client.export_sequence(slug, args.sequence, bin=args.bin)
        if args.wait:
            status = client.wait_export(slug, args.sequence, poll_s=args.poll, timeout_s=args.timeout or None)
            if status.get("status") == "done":
                emit(args, status, f"exported: media {status.get('media')} take {status.get('take')} (in the sequence's output bin; `post --attach {status.get('media')}` to deliver it)")
            else:
                emit(args, status, f"export {status.get('status')}: {status.get('error') or ''}".rstrip())
            return
        emit(args, status, f"export {status.get('status')} (`sequence status {args.sequence}` or `sequence export --wait` to follow it)")
        return
    if action == "status":
        emit(args, client.export_status(slug, args.sequence), None)
        return
    if action == "preview":
        data = client.preview_item(slug, args.sequence, args.bin, handles_s=args.handles)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        emit(args, {"sequence": args.sequence, "bin": args.bin, "handles_s": args.handles, "out": str(out), "bytes": len(data)},
             f"wrote {len(data)} bytes to {out}: the item with its layers and mix as the export will have them (+/- {args.handles:g}s of the take). QC it with `tool audio ...`, then `sequence mix {args.sequence} {args.bin} --reviewed <its start frame>`")
        return
    # everything else is a command through the ordinary write path
    name, payload = _sequence_payload(args)
    result = client.command(slug, name, _change_flags(args, payload), cursor=client.cursor)
    human = describe_result(result)
    if result.get("sequence"):
        human += f"\nsequence {result['sequence']}"
    emit(args, result, human)


def _sequence_payload(args) -> tuple[str, dict]:
    a = args.seq_action
    if a == "new":
        return "create_sequence", ({"sequence_name": args.name} if args.name else {})
    if a == "rename":
        return "set_sequence_field", {"sequence": args.sequence, "field": "name", "value": args.name}
    if a == "target":
        return "set_sequence_field", {"sequence": args.sequence, "field": "output_bin", "value": None if args.bin == "new" else args.bin}
    if a == "clone":
        return "clone_sequence", {"sequence": args.sequence}
    if a == "delete":
        return "delete_sequence", {"sequence": args.sequence, "delete_exports": bool(args.with_exports)}
    if a == "add":
        return "add_sequence_items", {"sequence": args.sequence, "bins": list(args.bins), **({"at": max(0, int(args.at) - 1)} if args.at is not None else {})}
    if a == "remove":
        return "remove_sequence_item", {"sequence": args.sequence, "bin": args.bin}
    if a == "order":
        return "reorder_sequence", {"sequence": args.sequence, "order": list(args.bins)}
    if a == "trim":
        return "set_item_trim", {"sequence": args.sequence, "bin": args.bin, "head_s": args.head, "tail_s": args.tail}
    if a == "still":
        return "set_item_still", {"sequence": args.sequence, "bin": args.bin, "still_s": args.seconds}
    if a == "layer-add":
        layer = {"id": args.layer_id, "media": args.media, "source_in_s": args.source_in, "start_frame": args.start_frame, "end": args.end, "gain_db": args.gain, "fade_in_s": args.fade_in, "fade_out_s": args.fade_out}
        return "set_sequence_layer", {"sequence": args.sequence, "layer": layer, "summary": args.summary}
    if a == "layer-drop":
        return "remove_sequence_layer", {"sequence": args.sequence, "layer": args.layer_id, **({"summary": args.summary} if args.summary is not None else {})}
    if a == "mix":
        payload: dict = {"sequence": args.sequence, "bin": args.bin}
        if args.clip_db is not None:
            payload["clip_db"] = args.clip_db
        layers: dict = {}
        for spec in args.layer or []:
            if "=" not in spec:
                raise UsageError(f"--layer wants id=dB or id=mute, got {spec!r}")
            key, value = spec.split("=", 1)
            if value.strip().lower() in ("mute", "muted", "off", "null"):
                layers[key.strip()] = None
            else:
                try:
                    layers[key.strip()] = float(value)
                except ValueError:
                    raise UsageError(f"--layer {key}: {value!r} is not a dB value or mute") from None
        if layers:
            payload["layers"] = layers
        if args.edge_fade_ms is not None:
            payload["edge_fade_ms"] = args.edge_fade_ms
        if args.ramp_ms is not None:
            payload["ramp_ms"] = args.ramp_ms
        if args.reviewed is not None:
            payload["reviewed_start_frame"] = args.reviewed
        if args.summary is not None:
            payload["summary"] = args.summary
        return "set_item_mix", payload
    if a in ("set", "layer"):
        try:
            props = json.loads(args.props)
        except ValueError as exc:
            raise UsageError(f"--props is not JSON: {exc}") from None
        if not isinstance(props, dict):
            raise UsageError("--props must be a JSON object")
        if a == "layer":
            return "set_sequence_agent", {"sequence": args.sequence, "props": props}
        return "set_item_agent", {"sequence": args.sequence, "bin": args.bin, "props": props}
    raise UsageError(f"unknown sequence action {a}")


POST_STATUS_WORDS = {"draft": "draft, not sent", "uploading": "uploading", "uploaded": "on the platform", "failed": "upload failed"}


def _post_line(p: dict) -> str:
    media = p.get("media_record") or {}
    where = f"{p.get('bin_name') or p.get('bin')}" + (f" ({media.get('type')}{', take ' + str(media['take']) if media.get('take') else ''})" if media else " (video deleted)")
    title = f'"{p["title"]}"' if p.get("title") else "(no title yet)"
    status = POST_STATUS_WORDS.get(p.get("status", ""), p.get("status", ""))
    tail = f"  -> {p['studio_url']}" if p.get("studio_url") else ""
    channel = p.get("channel_handle") or (f"channel {p['channel']} (not connected here)" if p.get("channel") else "no channel")
    return f"{p['id']}  {p.get('platform', 'youtube'):8} {channel:18} {status:16} {p.get('privacy', 'private'):8} {title}  from {where}{tail}"


def _check_lines(status: dict, project: dict | None) -> str:
    """`publish check`: the server's channels, and which one this project's posts go to."""
    if not status.get("configured"):
        return f"youtube: not configured -- {status.get('detail')}"
    chans = status.get("channels") or []
    lines = ["youtube: ready to upload"]
    if chans:
        lines.append("channels connected on the server: " + ", ".join(f"{c.get('handle') or c.get('title') or c['id']} ({c.get('title') or c['id']})" for c in chans))
    else:
        lines.append("no channel connected on the server yet: the user connects one in a project's settings (Project settings > YouTube), at the PC")
    if project is not None:
        rec = project.get("youtube_channel_record")
        if rec:
            lines.append(f"this project posts to {rec.get('handle') or rec.get('title') or rec['id']}")
        elif project.get("youtube_channel"):
            lines.append(f"this project's channel ({project['youtube_channel']}) is not connected here any more: the user picks another in the project settings; no post can be drafted until then")
        else:
            lines.append("this project has no channel yet: the user picks one in the project settings (Project settings > YouTube); no post can be drafted until then")
    return "\n".join(lines)


def _upload_line(status: dict) -> str:
    st = status.get("status")
    if st == "running":
        return f"uploading: {int((status.get('progress') or 0) * 100)}%"
    if st == "done":
        return f"uploaded, private: {status.get('remote_url')} -- the user finishes it in YouTube Studio: {status.get('studio_url')}"
    if st == "failed":
        return f"upload failed: {status.get('error')}"
    return "no upload running (the post's own status says what happened before this server started)"


def _read_text_arg(value: str | None, file: str | None, what: str) -> str | None:
    """A text given inline or as a file; the file wins. None when neither was given."""
    if file:
        try:
            return Path(file).read_text(encoding="utf-8")
        except OSError as exc:
            raise UsageError(f"cannot read --{what}-file {file}: {exc}") from None
    return value


def cmd_publish(client, args):
    """The publishing verbs: a post is a video with its title and description, drafted for a
    platform (YouTube) and held unsent. Phase 2 adds `publish upload`."""
    action = args.pub_action
    if action == "check":
        status = client.publish_status("youtube")
        project = client.project(args.project) if args.project else None
        emit(args, {**status, "project_channel": (project or {}).get("youtube_channel_record")}, _check_lines(status, project))
        return
    slug = need_project(args)
    if action is None or action == "list":
        posts = client.posts(slug, bin=getattr(args, "bin", None))
        emit(args, posts, "\n".join(_post_line(p) for p in posts) or "(no posts; `publish new --bin <id> --title ...` drafts one)")
        return
    if action == "show":
        p = client.post_detail(slug, args.post)
        head = _post_line(p)
        body = [head, f"description ({len(p.get('description') or '')} chars):", p.get("description") or "(none)"]
        if p.get("tags"):
            body.append("tags: " + ", ".join(p["tags"]))
        body.append(f"altered or synthetic content: {'yes' if p.get('synthetic') else 'no'}; made for kids: {'yes' if p.get('made_for_kids') else 'no'}")
        if p.get("thumbnail"):
            body.append(f"thumbnail: {p['thumbnail']}")
        if p.get("publish_at"):
            body.append(f"scheduled: {p['publish_at']}")
        if p.get("extra"):
            body.append("extra: " + ", ".join(f"{k}={v}" for k, v in p["extra"].items()))
        if p.get("error"):
            body.append(f"error: {p['error']}")
        emit(args, p, "\n".join(body))
        return
    if action == "new":
        description = _read_text_arg(args.description, args.description_file, "description")
        payload: dict = {"bin": args.bin, "platform": args.platform, "title": args.title or "", "description": description or ""}
        if args.media:
            payload["media"] = args.media
        if args.tags is not None:
            payload["tags"] = [t for t in args.tags.split(",")]
        result = client.command(slug, "create_post", _change_flags(args, payload), cursor=client.cursor)
        human = describe_result(result)
        if result.get("post"):
            human += f"\npost {result['post']} (draft, private, not sent; `publish show {result['post']}`)"
        emit(args, result, human)
        return
    if action == "delete":
        result = client.command(slug, "delete_post", _change_flags(args, {"post": args.post}), cursor=client.cursor)
        emit(args, result, describe_result(result))
        return
    if action == "status":
        status = client.upload_status(slug, args.post)
        emit(args, status, _upload_line(status))
        return
    if action == "upload":
        result = client.upload_post(slug, args.post, requested=bool(args.requested), reason=args.reason)
        human = describe_result(result)
        ch = result.get("change") or {}
        if (ch.get("decision") or {}).get("status") == "proposed":
            emit(args, result, human + "\ncard sent: the upload starts when the user says Yes (`watch`, then `publish status`)")
            return
        if args.wait:
            status = client.wait_upload(slug, args.post, poll_s=args.poll, timeout_s=args.timeout or None)
            emit(args, status, human + "\n" + _upload_line(status))
            return
        emit(args, result, human + f"\nuploading (`publish status {args.post}` or `publish upload --wait` to follow it)")
        return
    if action == "set":
        edits: list[tuple[str, Any]] = []
        if args.title is not None:
            edits.append(("title", args.title))
        description = _read_text_arg(args.description, args.description_file, "description")
        if description is not None:
            edits.append(("description", description))
        if args.tags is not None:
            edits.append(("tags", [t for t in args.tags.split(",")]))
        if args.privacy is not None:
            edits.append(("privacy", args.privacy))
        if args.synthetic is not None:
            edits.append(("synthetic", args.synthetic == "on"))
        if args.kids is not None:
            edits.append(("made_for_kids", args.kids == "on"))
        if args.media is not None:
            edits.append(("media", args.media))
        if args.thumbnail is not None:
            edits.append(("thumbnail", None if args.thumbnail.lower() in ("none", "null", "") else args.thumbnail))
        if args.publish_at is not None:
            edits.append(("publish_at", None if args.publish_at.lower() in ("none", "null", "") else args.publish_at))
        if args.extra is not None:
            try:
                extra = json.loads(args.extra)
            except ValueError as exc:
                raise UsageError(f"--extra is not JSON: {exc}") from None
            edits.append(("extra", extra))
        if not edits:
            raise UsageError("publish set: give at least one field (--title, --description[-file], --tags, --privacy, --synthetic, --kids, --media, --thumbnail, --publish-at, --extra)")
        results = []
        for field, value in edits:
            result = client.command(slug, "set_post_field", _change_flags(args, {"post": args.post, "field": field, "value": value}), cursor=client.cursor)
            results.append(result)
        emit(args, results if len(results) > 1 else results[0], "\n".join(describe_result(r) for r in results))
        return
    raise UsageError(f"unknown publish action {action}")


def cmd_jobs(client, args):
    jobs = client.jobs(need_project(args))
    emit(args, jobs, "\n".join(_job_line(j) for j in jobs) or "(no jobs)")


def cmd_end(client, args):
    slug = need_project(args)
    posted = None
    if args.summary:
        # the wrap-up is the next session's opening context; it has to land before the session closes
        posted = client.post_message(slug, args.summary, kind="agent", attachments=args.attach or None)
    result = client.end(slug)
    save_state(slug, session=None)
    human = describe_result(result)
    if posted:
        human = "summary posted\n" + human
    emit(args, {**result, "summary": posted}, human)


def cmd_thumb(client, args):
    slug = need_project(args)
    data = client.thumb(slug, args.media, size=args.size) if args.size != "file" else client.media_file(slug, args.media)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    emit(args, {"media": args.media, "size": args.size, "out": str(out), "bytes": len(data)}, f"wrote {len(data)} bytes to {out}")


# -- the tool kit -----------------------------------------------------------------


def tool_python(name: str) -> Path:
    """The tool's own interpreter: ``.venv/Scripts/python.exe`` here, ``.venv/bin/python`` elsewhere."""
    venv = TOOLS_DIR / name / ".venv"
    win = venv / "Scripts" / "python.exe"
    return win if win.exists() or os.name == "nt" else venv / "bin" / "python"


def tool_installed(name: str) -> bool:
    return tool_python(name).is_file() and (TOOLS_DIR / name / "run.py").is_file()


def installed_tools() -> list[str]:
    if not TOOLS_DIR.is_dir():
        return []
    return sorted(d.name for d in TOOLS_DIR.iterdir() if d.is_dir() and tool_installed(d.name))


def guide_first_line(name: str) -> str:
    guide = TOOLS_DIR / name / "GUIDE.md"
    if not guide.is_file():
        return "(no GUIDE.md)"
    for line in guide.read_text(encoding="utf-8", errors="replace").splitlines():
        text = line.strip().lstrip("#").strip()
        if text:
            return text.encode("ascii", "replace").decode("ascii")
    return "(empty guide)"


def cmd_tools(client, args):
    names = installed_tools()
    rows = [{"name": n, "guide": str(TOOLS_DIR / n / "GUIDE.md"), "about": guide_first_line(n)} for n in names]
    lines = [f"{r['name']:12} {r['about']}  (read {r['guide']} before first use)" for r in rows]
    emit(args, rows, "\n".join(lines) or f"(no tools installed under {TOOLS_DIR})")


def _fetch_media_arg(client, slug: str | None, token: str, scratch: Path) -> str:
    """A media id in a tool argument becomes a local file, fetched through the server (so the
    private-project gate applies). Lists ``m_a,m_b`` are fetched item by item."""
    parts = token.split(",") if "," in token else [token]
    if not any(MEDIA_ID_RE.match(p) for p in parts):
        return token
    out = []
    for part in parts:
        if not MEDIA_ID_RE.match(part):
            out.append(part)
            continue
        if not slug:
            raise UsageError(f"{part} looks like a media id; --project <slug> is needed to fetch it")
        record = client.media_record(slug, part)
        ext = Path(str(record.get("path") or "")).suffix or ".bin"
        dest = scratch / f"{part}{ext}"
        if not dest.is_file():
            dest.write_bytes(client.media_file(slug, part))
        out.append(str(dest))
    return ",".join(out)


def cmd_tool(client, args) -> int:
    """Run one verb of one tool in the tool's own venv, CPU only, with media ids fetched first.
    The tool's JSON goes to stdout as it is; its exit code is this call's."""
    name = args.name
    if not re.match(r"^[A-Za-z0-9_-]+$", name) or not tool_installed(name):
        print(f"ERROR: tool {name} is not installed (expected {TOOLS_DIR / name / 'run.py'} and its .venv); run `tools` for the installed ones", file=sys.stderr)
        return 2
    raw = list(args.args or [])
    if raw and raw[0] == "--":
        raw = raw[1:]
    # --scratch after the verb lands among the pass-through args; it is this verb's, not the tool's
    scratch_arg = args.scratch
    if "--scratch" in raw:
        at = raw.index("--scratch")
        if at + 1 < len(raw):
            scratch_arg = raw[at + 1]
        del raw[at : at + 2]
    scratch = Path(scratch_arg) if scratch_arg else Path(tempfile.mkdtemp(prefix=f"thm-tool-{name}-"))
    scratch.mkdir(parents=True, exist_ok=True)
    rest = [_fetch_media_arg(client, args.project, a, scratch) for a in raw]
    tool_dir = TOOLS_DIR / name
    env = dict(os.environ)
    env["CUDA_VISIBLE_DEVICES"] = ""
    env["HF_HOME"] = str(tool_dir / "models")
    env["TORCH_HOME"] = str(tool_dir / "models")
    # plain HTTPS for model downloads: the xet transport drops the connection on this network,
    # and the symlink cache layout needs a privilege a plain Windows account lacks
    env.setdefault("HF_HUB_DISABLE_XET", "1")
    env.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    cmd = [str(tool_python(name)), "-I", str(tool_dir / "run.py"), args.verb, *rest, "--scratch", str(scratch)]
    proc = subprocess.run(cmd, cwd=str(tool_dir), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = proc.stdout.strip()
    if out:
        print(out.encode("ascii", "replace").decode("ascii"))
    err = proc.stderr.strip()
    if err and (proc.returncode != 0 or os.environ.get("THM_TOOL_VERBOSE")):
        print(err.encode("ascii", "replace").decode("ascii")[-4000:], file=sys.stderr)
    return proc.returncode


HANDLERS = {
    "projects": cmd_projects, "create": cmd_create, "workflows": cmd_workflows, "loras": cmd_loras, "wait": cmd_wait, "connect": cmd_connect, "project": cmd_project, "bin": cmd_bin,
    "inbox": cmd_inbox, "watch": cmd_watch, "post": cmd_post, "blocker": cmd_blocker, "cmd": cmd_cmd, "generate": cmd_generate,
    "propose-shots": cmd_propose_shots,
    "jobs": cmd_jobs, "end": cmd_end, "thumb": cmd_thumb,
    "enhance-requests": cmd_enhance_requests, "enhance-reply": cmd_enhance_reply,
    "sessions": cmd_sessions, "mode": cmd_mode, "history": cmd_history, "recipe": cmd_recipe, "recall": cmd_recall, "check": cmd_check,
    "frames": cmd_frames, "guide": cmd_guide, "commands": cmd_commands, "upload": cmd_upload, "gif": cmd_gif, "status": cmd_status,
    "file": cmd_file, "keep": cmd_keep, "seen": cmd_seen, "notes": cmd_notes, "answer": cmd_answer, "resume": cmd_resume, "rules": cmd_rules,
    "withdraw": cmd_withdraw, "cancel": cmd_cancel, "restart-comfy": cmd_restart_comfy,
    "sequence": cmd_sequence, "tools": cmd_tools, "tool": cmd_tool, "publish": cmd_publish,
}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="THM agent client: commands over HTTP, no file access")
    ap.add_argument("--server", default=os.environ.get("THM_SERVER") or DEFAULT_SERVER)
    ap.add_argument("--project", help="project slug")
    ap.add_argument("--json", action="store_true", help="print the server's JSON")
    ap.add_argument("--cursor", type=int, help="override the saved inbox cursor")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("projects", help="list projects")
    p = sub.add_parser("create", help="harness-first: create an empty project, optionally with the brief")
    p.add_argument("--name", required=True)
    p.add_argument("--brief", default=None, help="the brief the user gave you; otherwise they write it in the app")
    p.add_argument("--settings", default=None, help='JSON: {"width","height","image_workflow","video_workflow","image_prompt",...}')
    p.add_argument("--audio", default=None, help="a song file the project is built from: uploaded into a Music bin tagged music (music.md)")
    p = sub.add_parser("workflows", help="the workflows the server offers, with their kind; `runs` lists the user's recent ComfyUI runs; `import NAME` tags and saves one (workflow-import.md)")
    p.add_argument("action", nargs="?", choices=["list", "runs", "import"], default="list")
    p.add_argument("name", nargs="?", help="import: the workflow's name in THM (file stem)")
    p.add_argument("--run", default=None, help="import: the ComfyUI run id (or a prefix) from `workflows runs`; default the newest")
    p.add_argument("--png", default=None, help="import: an output PNG carrying the graphs, instead of a run")
    p.add_argument("--kind", choices=["image", "video"], default=None, help="import: override the guessed kind")
    p.add_argument("--tag", action="append", default=[], metavar="NODE=TAG", help="import: give node NODE the title TAG (repeatable); wins over the proposal")
    p.add_argument("--untag", action="append", default=[], metavar="NODE", help="import: drop the proposed (or the user's) THM tag on NODE (repeatable)")
    p.add_argument("--system-prompt-file", default=None, help="import: the enhancer system prompt you researched for this model; required when the graph has no built-in enhancer")
    p.add_argument("--keep-builtin-enhancer", action="store_true", help="import: leave the graph's own LLM prompt rewrite in place")
    p.add_argument("--no-lora-marker", action="store_true", help="import: do not insert a THM-Lora marker after the model loader")
    p.add_argument("--dry-run", action="store_true", help="import: report the proposal, write nothing")
    p.add_argument("--replace", action="store_true", help="import: overwrite a workflow of the same name")
    p.add_argument("--limit", type=int, default=10, help="runs: how many")
    p = sub.add_parser("loras", help="the lora files the server can load, named exactly as a bin's loras field wants them")
    p.add_argument("--find", default=None, help="only names containing this text")
    p = sub.add_parser("wait", help="block until a project has a waiting session")
    p.add_argument("--poll", type=float, default=5.0)
    p.add_argument("--timeout", type=float, default=None)
    p = sub.add_parser("connect", help="connect to the waiting session")
    p.add_argument("--harness", default="claude-code")
    sub.add_parser("project", help="project detail")
    p = sub.add_parser("bin", help="bin detail: recipe, gallery, references with handles, capacity, active job")
    p.add_argument("bin")
    sub.add_parser("inbox", help="messages since the saved cursor; saves the new cursor")
    p = sub.add_parser("watch", help="block until the user writes or decides (one project with --project, else whichever project needs you first); leaves the cursor for inbox to read. Returns 'quiet' every --timeout seconds: run it again, that is the idle loop")
    p.add_argument("--poll", type=float, default=3.0)
    p.add_argument("--timeout", type=float, default=540.0, help="seconds before a quiet return (default 9 min, under a harness's 10-minute command limit); 0 = forever")
    p.add_argument("--jobs", default=None, help="generation ids, comma separated: also return when any of these renders finishes or fails (the one wait while renders run: a note still wakes you first)")
    sub.add_parser("sessions", help="every project with its session status, mode, blocker and pending count")
    p = sub.add_parser("mode", help="set the session mode: auto (full auto: loop; the finished candidate is the card), manual (approve every: every render proposed first), image_review (approve all output: every take is a card), video_review (approve all videos: every video take is a card), plan (plan only: revise the brief). --drafts on|off is a separate switch that rides with any mode")
    p.add_argument("mode", nargs="?", choices=["auto", "manual", "image_review", "video_review", "plan"])
    p.add_argument("--drafts", choices=["on", "off"], default=None, help="drafts first: cheap renders run as the mode says; a full-quality render is asked about first")
    p.add_argument("--requested", action="store_true", help="the user asked for the switch in the feed")
    p = sub.add_parser("history", help="a bin's takes as an experiment log: params, QC marks, and the prompt diff between takes")
    p.add_argument("--bin", required=True)
    p.add_argument("--limit", type=int, default=None, help="newest N")
    p = sub.add_parser("recipe", help="how a media was made: workflow, prompt, references, params")
    p.add_argument("media")
    p = sub.add_parser("recall", help="put the bin back to the recipe that made a take (the recipe is in the media's record; never ask the user for it); --generate renders it again on the same seed")
    p.add_argument("media")
    p.add_argument("--bin", default=None, help="the bin to restore into; default: the bin the take was made in")
    p.add_argument("--generate", action="store_true", help="render once more on the recalled recipe, same seed and length")
    p.add_argument("--reason", default=None)
    p = sub.add_parser("check", help="the bin's .handles against its references: unknown tokens, unused handles, start/end positions")
    p.add_argument("--bin", required=True)
    p = sub.add_parser("frames", help="a contact sheet of a video's frames, times stamped, for QC")
    p.add_argument("media")
    p.add_argument("--out", required=True)
    p.add_argument("--n", type=int, default=4, help="frames, 1-8")
    p.add_argument("--size", type=int, default=1024, help="long edge per frame")
    p = sub.add_parser("guide", help="a workflow's prompt guide: the rules the Enhance button rewrites by")
    p.add_argument("workflow")
    p = sub.add_parser("commands", help="every command and its payload fields; one name for its detail")
    p.add_argument("name", nargs="?")
    p = sub.add_parser("upload", help="put a local file into a bin, with where it came from when you made it from a take")
    p.add_argument("file")
    p.add_argument("--bin", default=None)
    p.add_argument("--bin-name", dest="bin_name", default=None)
    p.add_argument("--from", dest="source", default=None, help="the media this was made from")
    p.add_argument("--note", default=None, help="what was done, e.g. 'audio removed, ffmpeg -an'")
    p = sub.add_parser("gif", help="export a video take as an animated GIF into a bin (made on the server; the user downloads it from the bin)")
    p.add_argument("media", help="the video media id")
    p.add_argument("--bin", default=None, help="the bin it lands in; default: a new bin-n")
    p.add_argument("--bin-name", dest="bin_name", default=None)
    p.add_argument("--start", type=float, default=None, help="seconds; default the start")
    p.add_argument("--end", type=float, default=None, help="seconds; default the end")
    p.add_argument("--fps", type=float, default=None, help="frame rate; default the source's")
    p.add_argument("--width", type=int, default=None, help="px; height follows; default the source's")
    p = sub.add_parser("file", help="download a file a note carries ([files: f_...] on an inbox line): the user's own picture, in no bin; or a take's file by its media id (m_...)")
    p.add_argument("file", help="f_... (a note's file) or m_... (a take)")
    p.add_argument("--out", required=True)
    p = sub.add_parser("seen", help="clear the user's blue NEW markers on media they have effectively seen (an assembly that contains these takes was delivered); everything those were made from clears too")
    p.add_argument("media", nargs="*", help="media ids")
    p.add_argument("--all", action="store_true", help="flip every new marker in the project off")
    p = sub.add_parser("keep", help="make a note's file project media: it lands in a bin like an upload (only when the user wants it in the project)")
    p.add_argument("file")
    p.add_argument("--bin", default=None)
    p.add_argument("--bin-name", dest="bin_name", default=None)
    sub.add_parser("notes", help="the user's notes you have read but not dealt with: your to-do list; watch does not block while any are open")
    sub.add_parser("resume", help="the reconnect summary: mode, open notes, renders running or cut off by a restart, your last post, the project's rules (connect prints it too)")
    sub.add_parser("rules", help="the project's standing rules (QC actions, continuity): add with `cmd add_project_rule`, retire with `cmd remove_project_rule`")
    p = sub.add_parser("answer", help="close notes with what you did about them; the user reads it on the note")
    p.add_argument("notes", nargs="+", help="message ids, from inbox or notes")
    p.add_argument("--did", required=True, help='a few words: "quarter re-render queued", "fixed in take 11", "waiting on your yes on the P05 card"')
    p = sub.add_parser("withdraw", help="take back one of your own cards still waiting for the user (a card posted in a mode that needed none): it closes with your reason on it; nothing runs, nothing is undone")
    p.add_argument("change", help="the card's change id")
    p.add_argument("--reason", default=None, help="why, in a few words; the user reads it on the closed card")
    p = sub.add_parser("cancel", help="stop a live render (the runner, and the prompt in ComfyUI). On your own only in an auto session; in any other mode only with --requested, when the user asked for it")
    p.add_argument("job", help="the generation id (jobs lists them)")
    p.add_argument("--requested", action="store_true", help="the user asked for this in the feed")
    p.add_argument("--reason", default=None, help="why; goes in the feed")
    p = sub.add_parser("restart-comfy", help="stop and start ComfyUI through the server (the user's own restart script); live renders are queued again and start when it answers. On your own only in an auto session on --project; otherwise only with --requested")
    p.add_argument("--requested", action="store_true", help="the user asked for this in the feed")
    p.add_argument("--reason", default=None, help="why (a stalled render, ComfyUI not answering); goes in the feed")
    p = sub.add_parser("post", help="post an agent message")
    p.add_argument("text")
    p.add_argument("--attach", action="append", help="media id to attach (repeatable)")
    p = sub.add_parser("blocker", help="post a blocker: the user must act before you continue")
    p.add_argument("text")
    p.add_argument("--attach", action="append")
    p = sub.add_parser("status", help="what you are doing right now, for the strip (no feed line): 'rewriting the prompt for Video 6'")
    p.add_argument("text")
    sub.add_parser("enhance-requests", help="prompt rewrites the app is waiting on you for (system prompt + message each)")
    p = sub.add_parser("enhance-reply", help="answer an enhance request with the rewritten prompt")
    p.add_argument("--id", required=True)
    p.add_argument("--text", help="the rewritten prompt")
    p.add_argument("--file", help="read the rewritten prompt from this file instead")
    p.add_argument("--error", help="why no prompt could be produced")
    p = sub.add_parser("cmd", help="run any command with a JSON payload")
    p.add_argument("name")
    p.add_argument("--payload", default="{}")
    p.add_argument("--propose", action="store_true", help="ask before applying: the card lands and nothing runs until the user approves")
    p.add_argument("--requested", action="store_true", help="the user asked for this in the feed: land it done, no approval card")
    p.add_argument("--quiet", action="store_true", help="work in progress: record it, no feed line, no verdict owed (wins over --card)")
    p.add_argument("--card", action="store_true", help="ask anyway: this change owes a verdict and shows as a card (not with --quiet)")
    p.add_argument("--reason", default=None, help="why, in full sentences; shown on the card or the feed line (same as a reason key in the payload)")
    p = sub.add_parser("propose-shots", help="the pre-generation card: a batch of intended renders the user keeps, edits or drops; the approval makes the bins and queues the renders")
    p.add_argument("--file", default=None, help="JSON: a list of items, or {title, items}; - reads stdin")
    p.add_argument("--items", default=None, help="the same JSON inline")
    p.add_argument("--title", default=None, help="the batch in a few words, shown on the card")
    p.add_argument("--reason", default=None, help="why these, in full sentences; shown on the card")
    p.add_argument("--requested", action="store_true", help="the user asked for the batch in the feed: no card, bins and renders land at once")
    p.add_argument("--wait", action="store_true", help="block until the user decides, then print the outcome per item")
    p.add_argument("--show", default=None, metavar="CHANGE", help="print the outcome of an earlier card by its change id")
    p.add_argument("--poll", type=float, default=3.0)
    p.add_argument("--timeout", type=float, default=None)
    p = sub.add_parser("generate", help="queue a render of a bin's recipe (needs a current inbox cursor)")
    p.add_argument("--bin", required=True)
    p.add_argument("--params", default=None, help='JSON, e.g. {"seed": 7}; see params.md')
    p.add_argument("--reason", default=None, help="why this render, what one thing changed, whether the seed is held; shown in the feed")
    p.add_argument("--propose", action="store_true")
    p.add_argument("--requested", action="store_true", help="the user asked for this in the feed: land it done, no approval card")
    p.add_argument("--card", action="store_true", help="ask anyway: this render owes a verdict")
    p.add_argument("--wait", action="store_true", help="wait for the render, but return early when a note, a decision or a mode change arrives (it says so; the render keeps going)")
    p.add_argument("--poll", type=float, default=2.0)
    p.add_argument("--timeout", type=float, default=None)
    p = sub.add_parser("sequence", help="the sequence: an ordered list of bins that plays each bin's current take and exports to one mp4 in its output bin. Alone: list the sequences")
    sq = p.add_subparsers(dest="seq_action")
    sq.add_parser("list", help="every sequence: id, name, items, length, output bin")
    q = sq.add_parser("show", help="one sequence, a row per item: what plays, length, trims, your settings")
    q.add_argument("sequence")
    q = sq.add_parser("new", help="an empty sequence (Sequence N); its exports bin is made at the first export unless `target` picks one")
    q.add_argument("--name", default=None)
    q = sq.add_parser("rename", help="the sequence's name; its output bin keeps its own")
    q.add_argument("sequence")
    q.add_argument("name")
    q = sq.add_parser("target", help="the bin exports land in: any bin not in the sequence, or `new` for a fresh <name> Exports at the next export")
    q.add_argument("sequence")
    q.add_argument("bin")
    q = sq.add_parser("clone", help="a copy with the same items, trims, still lengths and your settings; a new empty output bin")
    q.add_argument("sequence")
    q = sq.add_parser("delete", help="remove the sequence; the bins in it stay. --with-exports takes its output bin and media too")
    q.add_argument("sequence")
    q.add_argument("--with-exports", dest="with_exports", action="store_true")
    q = sq.add_parser("add", help="add bins, in this order; each plays its current take; a bin sits in a sequence once. Appended, or --at N to insert before item N (1-based)")
    q.add_argument("sequence")
    q.add_argument("bins", nargs="+")
    q.add_argument("--at", type=int, default=None, help="1-based position to insert before")
    q = sq.add_parser("remove", help="take a bin out; the bin and its takes stay")
    q.add_argument("sequence")
    q.add_argument("bin")
    q = sq.add_parser("order", help="the whole order, every bin id in the sequence")
    q.add_argument("sequence")
    q.add_argument("bins", nargs="+")
    q = sq.add_parser("trim", help="seconds off the head and tail of a video item; the take stays whole. Both 0 = the full take")
    q.add_argument("sequence")
    q.add_argument("bin")
    q.add_argument("--head", type=float, default=0.0)
    q.add_argument("--tail", type=float, default=0.0)
    q = sq.add_parser("still", help="how long an image item shows (min 0.5s; default 3s)")
    q.add_argument("sequence")
    q.add_argument("bin")
    q.add_argument("seconds", type=float)
    q = sq.add_parser("set", help="your settings on ONE item the app has no control for (a mute, a pop removal, a dissolve out); merged, null removes a key; the user sees them read-only. Must carry \"summary\": that item alone, times from its start")
    q.add_argument("sequence")
    q.add_argument("bin")
    q.add_argument("--props", required=True, help="a JSON object")
    q = sq.add_parser("layer", help="your settings that span the whole sequence (a walla bed, room tone, music); merged, null removes a key; shown once under the player. Must carry \"summary\"")
    q.add_argument("sequence")
    q.add_argument("--props", required=True, help="a JSON object")
    q = sq.add_parser("layer-add", help="lay a song or a bed under the sequence (add, or replace the layer with this id): it runs in one piece from --in at --start-frame until the picture ends (or the source, --end source). Per-shot levels are `mix`. --summary is the user's line under the player, required")
    q.add_argument("sequence")
    q.add_argument("layer_id", help="your name for it: music, walla, room-tone")
    q.add_argument("--media", required=True, help="an audio media id, or a video media with sound")
    q.add_argument("--in", dest="source_in", type=float, default=0.0, help="where in the source it starts playing, seconds")
    q.add_argument("--start-frame", dest="start_frame", type=int, default=0, help="the sequence frame it starts on")
    q.add_argument("--end", choices=["picture", "source"], default="picture")
    q.add_argument("--gain", type=float, default=0.0, help="dB")
    q.add_argument("--fade-in", dest="fade_in", type=float, default=0.0, help="seconds")
    q.add_argument("--fade-out", dest="fade_out", type=float, default=0.0, help="seconds")
    q.add_argument("--summary", required=True, help="what is under the sequence now, for the user")
    q = sq.add_parser("layer-drop", help="take a layer off the sequence; every shot's offset for it goes too")
    q.add_argument("sequence")
    q.add_argument("layer_id")
    q.add_argument("--summary", default=None, help="what the user hears now; required unless nothing is left, then \"\" clears the line")
    q = sq.add_parser("mix", help="one shot's sound against the layers: --clip-db for its own audio, --layer music=-8 or --layer music=mute per layer, --edge-fade-ms, --ramp-ms, and --reviewed <start frame> once you have listened to it in place (sequence preview). --summary is the shot's line, required when the mix departs from the defaults")
    q.add_argument("sequence")
    q.add_argument("bin")
    q.add_argument("--clip-db", dest="clip_db", type=float, default=None)
    q.add_argument("--layer", action="append", default=None, help="id=dB, or id=mute; repeatable")
    q.add_argument("--edge-fade-ms", dest="edge_fade_ms", type=int, default=None)
    q.add_argument("--ramp-ms", dest="ramp_ms", type=int, default=None)
    q.add_argument("--reviewed", type=int, default=None, help="the shot's start frame when you QC'd it against the layers")
    q.add_argument("--summary", default=None)
    q = sq.add_parser("export", help="render the sequence to one mp4 at the project canvas; lands as the next take in the output bin")
    q.add_argument("sequence")
    q.add_argument("--bin", default=None, help="land it in this bin from now on (same as `target` first)")
    q.add_argument("--wait", action="store_true", help="block until it lands (or fails) and print the media id")
    q.add_argument("--poll", type=float, default=1.0)
    q.add_argument("--timeout", type=float, default=540.0, help="seconds; 0 = forever")
    q = sq.add_parser("status", help="where the export stands")
    q.add_argument("sequence")
    q = sq.add_parser("preview", help="one item rendered with its layer slices and mix through the export's own graph: what you hear is what the export will have. A scratch mp4 for your QC listen, not media; --handles adds up to 5 s of the take either side")
    q.add_argument("sequence")
    q.add_argument("bin")
    q.add_argument("--out", required=True)
    q.add_argument("--handles", type=float, default=0.0, help="seconds of the take before and after the item, as far as it has them")
    for q in sq.choices.values():
        if q.prog.split()[-1] in ("new", "rename", "target", "clone", "delete", "add", "remove", "order", "trim", "still", "set", "layer", "layer-add", "layer-drop", "mix"):
            q.add_argument("--reason", default=None, help="why, in full sentences; the feed line carries it")
            q.add_argument("--card", action="store_true", help="ask anyway: this change owes a verdict and shows as a card")
            q.add_argument("--quiet", action="store_true", help="work in progress: no feed line")
            q.add_argument("--requested", action="store_true", help="the user asked for this in the feed")
    p = sub.add_parser("publish", help="publishing: a post is a video with its title and description, drafted for a platform (YouTube) and held private and unsent until the user says to upload. Alone: list the posts")
    pb = p.add_subparsers(dest="pub_action")
    q = pb.add_parser("list", help="every post, newest first: status, privacy, title, the bin and video it is about")
    q.add_argument("--bin", default=None, help="only the posts drafted from this bin")
    q = pb.add_parser("show", help="one post with its full description, tags and flags")
    q.add_argument("post")
    q = pb.add_parser("new", help="draft a post for a bin's current video (or --media, a video in that bin). Nothing is sent; the draft is private")
    q.add_argument("--bin", required=True)
    q.add_argument("--media", default=None, help="a video in that bin, when not its current one")
    q.add_argument("--platform", default="youtube", choices=["youtube"])
    q.add_argument("--title", default=None, help="YouTube: up to 100 characters, no < or >")
    q.add_argument("--description", default=None, help="inline; prefer --description-file for anything with line breaks")
    q.add_argument("--description-file", dest="description_file", default=None, help="a UTF-8 text file; up to 5000 bytes, no < or >")
    q.add_argument("--tags", default=None, help="comma-separated; up to 500 characters together")
    q = pb.add_parser("set", help="change fields of a draft, one command per field given. Refused once the post is uploaded (finish it in the platform's app)")
    q.add_argument("post")
    q.add_argument("--title", default=None)
    q.add_argument("--description", default=None)
    q.add_argument("--description-file", dest="description_file", default=None)
    q.add_argument("--tags", default=None, help="comma-separated; the whole list")
    q.add_argument("--privacy", default=None, choices=["private", "unlisted"], help="never public from here: the user publishes")
    q.add_argument("--synthetic", default=None, choices=["on", "off"], help="YouTube's altered-or-synthetic disclosure; on by default")
    q.add_argument("--kids", default=None, choices=["on", "off"], help="made for kids; off by default")
    q.add_argument("--media", default=None, help="another video in the post's bin")
    q.add_argument("--thumbnail", default=None, help="an image media id, or none")
    q.add_argument("--publish-at", dest="publish_at", default=None, help="ISO 8601 (2026-11-02T17:00:00Z) to schedule, or none; the post stays private until then")
    q.add_argument("--extra", default=None, help="a JSON object of platform knobs (category, playlist); merged, null removes a key")
    q = pb.add_parser("delete", help="drop a post; the video stays in its bin")
    q.add_argument("post")
    q = pb.add_parser("upload", help="send the post's video to YouTube, private, with the words on the record. A card the user answers Yes to, unless --requested (the user asked for it in the feed), which starts it at once")
    q.add_argument("post")
    q.add_argument("--wait", action="store_true", help="with --requested: block until it lands (or fails) and print the links")
    q.add_argument("--poll", type=float, default=2.0)
    q.add_argument("--timeout", type=float, default=0.0, help="seconds; 0 = forever")
    q = pb.add_parser("status", help="where a post's upload stands")
    q.add_argument("post")
    pb.add_parser("check", help="can this server upload to YouTube (the client file present), which channels are connected, and which one this project posts to; the fix when not")
    for q in pb.choices.values():
        if q.prog.split()[-1] in ("new", "set", "delete", "upload"):
            q.add_argument("--reason", default=None, help="why, in full sentences; the feed line carries it")
            q.add_argument("--card", action="store_true", help="ask anyway: this change owes a verdict and shows as a card")
            q.add_argument("--quiet", action="store_true", help="work in progress: no feed line")
            q.add_argument("--requested", action="store_true", help="the user asked for this in the feed")
    sub.add_parser("jobs", help="render jobs known to this server process")
    p = sub.add_parser("end", help="end the session, after posting the wrap-up the next session opens with")
    p.add_argument("--summary", default=None, help="what worked, what is open, which takes are the picks")
    p.add_argument("--attach", action="append", help="media id to attach to the summary (repeatable)")
    p = sub.add_parser("thumb", help="download a thumbnail (or the file) for QC")
    p.add_argument("media")
    p.add_argument("--out", required=True)
    p.add_argument("--size", default="grid", choices=["grid", "detail", "poster", "file"])
    sub.add_parser("tools", help="the installed tools (thm-tools/<name>/ with a .venv and run.py), each with the first line of its GUIDE.md; read the guide before first use")
    p = sub.add_parser("tool", help="run a tool verb: `tool audio transcribe m_123 --expect \"the line\"`. Media ids among the arguments are fetched through the server first; the tool runs CPU-only in its own venv, prints JSON, and its exit code is yours. Tools measure; the project changes only through commands")
    p.add_argument("name", help="the tool, a folder under thm-tools/")
    p.add_argument("verb", help="the tool's verb; its GUIDE.md lists them")
    p.add_argument("--scratch", default=None, help="where the tool writes its files; default: a fresh temp folder, named in the JSON")
    p.add_argument("args", nargs=argparse.REMAINDER, help="the verb's arguments, passed through")
    return ap


def main(argv: list[str] | None = None, *, transport=None) -> int:
    args = build_parser().parse_args(argv)
    saved = load_state(args.project) if args.project else {}
    cursor = args.cursor if args.cursor is not None else saved.get("cursor")
    doing = DOING.get(args.command)
    if args.command == "cmd":
        doing = DOING_CMD.get(args.name) or args.name.replace("_", " ")
    # always the agent: no flag puts this CLI in the user's seat, so the private-project gate cannot be talked around
    client = ThmClient(args.server, session=saved.get("session"), cursor=cursor, transport=transport, doing=doing, mode=saved.get("mode"))
    try:
        rc = HANDLERS[args.command](client, args)
        if isinstance(rc, int) and rc != 0:
            return rc
        if args.project and client.mode and client.mode != saved.get("mode"):
            # the mode this agent has now seen; the next changing command carries it
            save_state(args.project, mode=client.mode)
            if saved.get("mode") and args.command != "inbox":
                print(f"MODE CHANGED on {args.project}: {saved.get('mode')} -> {client.mode}. Re-plan for {client.mode} before the next step", file=sys.stderr)
    except ThmError as exc:
        print(f"ERROR {exc.status} {exc.code}: {exc.detail}", file=sys.stderr)
        if exc.code == "mode_changed":
            # not seen yet: every changing call (posts too) stays refused until `inbox` is read,
            # so the rest of a chained shell line cannot run on the old plan
            verb = getattr(args, "name", None) or args.command
            print(
                f"MODE CHANGED to {exc.extra.get('mode')}: this {verb} did not run, and no command or post will until you run "
                "`inbox` (unpiped: read every item, the user may have answered too). Re-plan for the new mode, then act. "
                "Anything earlier in the same shell line already ran.",
                file=sys.stderr,
            )
        elif exc.code == "inbox_behind":
            msgs = exc.extra.get("messages") or []
            print("unread items (run inbox, then retry):", file=sys.stderr)
            print(describe_messages(msgs), file=sys.stderr)
        elif exc.extra:
            print(json.dumps(exc.extra, ensure_ascii=True), file=sys.stderr)
        return 1
    except UsageError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except TimeoutError as exc:
        print(f"ERROR timeout: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"ERROR cannot reach {args.server}: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
