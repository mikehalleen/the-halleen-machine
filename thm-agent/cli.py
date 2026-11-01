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
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from thm_client import PARAM_KEYS, ThmClient, ThmError, check_tokens, unknown_params  # noqa: E402

STATE_DIR = HERE / ".state"
DEFAULT_SERVER = "http://127.0.0.1:8010"

# Every command the server accepts and its payload, kept here so ``commands`` can answer offline
# and the skill's table has one source. tests/test_thm_client.py diffs it against
# thm_core.commands.COMMANDS, so a command added there without a line here fails the build.
# Every command also takes ``reason``, ``requested``, ``quiet`` and ``card``.
SETTABLE_FIELDS = ("name", "workflow", "length_s", "prompt", "negative", "loras", "selected", "description", "tags")

# What each subcommand says it is doing, sent as a header on every call so the app's strip can
# show the user the agent is on it. ``status`` sets a phrase of the agent's own for longer steps.
DOING = {
    "inbox": "reading your messages", "watch": "waiting for you", "project": "reading the project", "bin": "reading a bin",
    "history": "reading the take history", "recipe": "reading a recipe", "check": "checking the prompt's handles",
    "guide": "reading the workflow's prompt guide", "generate": "queuing a render", "jobs": "checking renders",
    "thumb": "looking at a take", "frames": "looking at a clip", "post": "writing to you", "blocker": "writing to you",
    "propose-shots": "proposing shots",
    "enhance-requests": "picking up an enhance request", "enhance-reply": "answering an enhance request",
    "upload": "uploading a file", "end": "wrapping up", "connect": "connecting",
}
DOING_CMD = {
    "generate": "queuing a render", "propose_shots": "proposing shots", "mark_take": "marking a take", "set_bin_field": "editing a bin", "create_bin": "making a bin",
    "bind_reference": "binding a reference", "rebind_reference": "binding a reference", "unbind_reference": "unbinding a reference",
    "post_message": "writing to you", "set_project_settings": "configuring the project", "delete_bin": "deleting a bin",
}
COMMAND_TABLE: dict[str, tuple[tuple[str, str], ...]] = {
    "create_bin": (("bin_name", "default bin-n"), ("workflow", "default: the project's image workflow"), ("length_s", "video only"), ("tags", "list")),
    "duplicate_bin": (("bin", ""), ("with_media", "copy the gallery too; default false")),
    "set_bin_field": (("bin", ""), ("field", "one of " + " ".join(SETTABLE_FIELDS)), ("value", "")),
    "bind_reference": (("bin", ""), ("media", ""), ("position", "1-based; start frame is 1, end frame 2 on video workflows that support them")),
    "unbind_reference": (("bin", ""), ("reference", "")),
    "rebind_reference": (("bin", ""), ("reference", ""), ("media", "same handle, different picture")),
    "reorder_references": (("bin", ""), ("order", "every reference id")),
    "recall_generation": (("bin", ""), ("from_media", ""), ("workflow", ""), ("prompt", ""), ("negative", ""), ("references", "[{media, handle}]"), ("loras", "")),
    "generate": (("bin", ""), ("params", "see params.md; {\"quarter\": true} for a draft video")),
    "propose_shots": (
        ("title", "the batch, in a few words"),
        ("items", "[{name | bin, description, prompt?, negative?, workflow?, references [media ids], length_s?, quarter, params, tags}]; the propose-shots verb reads them from a file"),
    ),
    "mark_take": (("media", ""), ("status", "pass | fail | in_progress"), ("note", "what changed, what it fixed, what it broke, what is open")),
    "add_media_to_bin": (("bin", ""), ("media", "the arrival becomes selected")),
    "remove_media_from_bin": (("bin", ""), ("media", "the record stays")),
    "delete_media": (("media", "everywhere, cascading"),),
    "delete_bin": (("bin", "its media go too, unless another bin holds or references them"),),
    "set_project_settings": (("project_name", ""), ("brief", "the revised brief a plan session proposes"), ("width", ""), ("height", ""), ("image_workflow", ""), ("video_workflow", ""), ("video_length_s", ""), ("image_prompt", ""), ("video_prompt", ""), ("image_negative", ""), ("video_negative", ""), ("private", "true | false; no cover on the project list, and the agent cannot see the project")),
    "post_message": (("text", ""), ("attachments", "media ids"), ("kind", "agent | blocker"), ("context", "user only: the bin/media the note is about")),
    "start_session": (("notes", ""), ("refs_bin", ""), ("mode", "auto | manual | image_review | video_review | plan; user only")),
    "set_session_mode": (("mode", "auto | manual | image_review | video_review | plan"),),
    "connect_session": (("harness", ""),),
    "end_session": (),
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


def describe_messages(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        kind = m.get("kind", "?")
        text = (m.get("text") or "").replace("\n", " ")
        tail = ""
        if m.get("attachments"):
            tail += f" [attachments: {', '.join(m['attachments'])}]"
        ctx = m.get("context")
        if ctx and ctx.get("bin"):
            # what the user was looking at: "this take" / "this video" in the text means these ids
            about = f"bin {ctx['bin']}"
            if ctx.get("bin_name"):
                about += f' "{ctx["bin_name"]}"'
            if ctx.get("media"):
                about += f" - media {ctx['media']}"
                if ctx.get("media_label"):
                    about += f" ({ctx['media_label']})"
            tail += f" [about: {about}]"
        if m.get("change"):
            tail += f" [change {m['change']}"
            if m.get("status"):
                tail += f" {m['status']}"
            if m.get("note"):
                tail += f": {m['note']}"
            tail += "]"
        lines.append(f"{m.get('ts', '')} {kind:8} {text}{tail}")
    return "\n".join(lines) if lines else "(no messages)"


def warn(text: str) -> None:
    """Advisory lines go to stderr so ``--json`` stdout stays parseable."""
    print(f"warning: {text}", file=sys.stderr)


def describe_check(result: dict, references: list[dict]) -> str:
    lines = []
    for r in references:
        media = r.get("media") or {}
        lines.append(f"  .{r.get('handle')} ordinal={r.get('ordinal')} {r.get('sub_label') or '-'} media={media.get('id') or 'missing'} from={r.get('bin_name')}")
    for h in result["unknown"]:
        lines.append(f"  unknown .{h}: the prompt names it, nothing is bound to it")
    for h in result["unused"]:
        lines.append(f"  unused .{h}: bound, never mentioned in the prompt")
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
        warn(f"unknown_token .{h} matches no reference in this bin")
    for h in res["unused"]:
        warn(f"unused_handle .{h} appears nowhere in the prompt")
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
        lines.append(f"generation {gen['id']} {gen.get('status')} seed={gen.get('params', {}).get('seed')}")
    for w in result.get("warnings") or []:
        lines.append(f"warning: {w.get('kind')} {w.get('detail')}")
    for m in result.get("messages") or []:
        lines.append(f"message {m.get('kind')}: {m.get('text')}")
    lines.append(f"project version {result.get('project_version')}")
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
    emit(args, detail, f"created {detail['slug']}\n" + describe_project(detail) + "\nnext: the user starts a session in the app; run wait")


def cmd_workflows(client, args):
    wfs = client.workflows()
    emit(args, wfs, "\n".join(f"{w['kind']:5} {w['name']}  slots={w.get('image_slots', 0)}i/{w.get('video_slots', 0)}v negative={w.get('has_negative')}" for w in wfs) or "(no workflows)")


def cmd_wait(client, args):
    detail = client.wait_for_session(args.project, poll_s=args.poll, timeout_s=args.timeout)
    save_state(detail["slug"], session=(detail.get("agent_session") or {}).get("id"))
    emit(args, detail, describe_project(detail))


def cmd_connect(client, args):
    slug = need_project(args)
    result = client.connect(slug, args.harness)
    save_state(slug, session=client.session)
    emit(args, result, describe_result(result))


def cmd_project(client, args):
    detail = client.project(need_project(args))
    emit(args, detail, describe_project(detail))


def cmd_bin(client, args):
    slug = need_project(args)
    detail = client.bin(slug, args.bin)
    b = detail["bin"]
    lines = [
        f"{b['name']} ({b['id']}) {detail.get('kind') or '-'} workflow={b.get('workflow') or '-'} length_s={b.get('length_s')}",
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
        lines.append(f"  ref {r['id']} .{r['handle']} media={media.get('id') or 'missing'} from={r.get('bin_name')} {r.get('sub_label') or ''}".rstrip())
    cap = detail.get("capacity") or {}
    lines.append(f"capacity: image_slots={cap.get('image_slots')} video_slots={cap.get('video_slots')} start={cap.get('supports_start_frame')} end={cap.get('supports_end_frame')}")
    for j in detail.get("active_jobs") or []:
        lines.append(f"active job {j['job']} {j['status']} {j.get('progress')}% {j.get('task') or ''}")
    if detail["bin"].get("unseen"):
        lines.append(f"unseen: {' '.join(detail['bin']['unseen'])}")
    emit(args, detail, "\n".join(lines))


def cmd_inbox(client, args):
    slug = need_project(args)
    messages, cursor = client.inbox(slug)
    save_state(slug, cursor=cursor)
    emit(args, {"messages": messages, "cursor": cursor}, describe_messages(messages) + f"\ncursor {cursor}")


def cmd_watch(client, args):
    if args.project:
        slug = args.project
        cursor = load_state(slug).get("cursor")
        messages = client.wait_for_inbox(slug, cursor=cursor, poll_s=args.poll, timeout_s=args.timeout)
        emit(args, {"project": slug, "messages": messages}, describe_messages(messages) + "\nnext: run inbox to read these and move the cursor")
        return
    # no project: every project is yours; whichever needs you first wakes you
    cursors = {}
    if STATE_DIR.is_dir():
        for path in STATE_DIR.glob("*.json"):
            cursors[path.stem] = load_state(path.stem).get("cursor")
    woke = client.wait_for_any(cursors, poll_s=args.poll, timeout_s=args.timeout)
    human = f"project {woke['slug']}: {woke['why']}\n" + describe_messages(woke["messages"])
    tail = "\nnext: connect" if woke["why"] == "session waiting" else f"\nnext: --project {woke['slug']} inbox"
    emit(args, woke, human + tail)


def cmd_sessions(client, args):
    rows = client.sessions()
    lines = []
    for r in rows:
        line = f"{r['slug']:24} {r['status']:8} mode={r.get('mode') or '-':7} harness={r.get('harness') or '-':12} pending={r['pending_changes']}"
        if r.get("blocker"):
            line += f"  BLOCKER: {r['blocker']}"
        lines.append(line)
    emit(args, rows, "\n".join(lines) or "(no projects)")


def cmd_mode(client, args):
    slug = need_project(args)
    result = client.set_mode(slug, args.mode, requested=args.requested)
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
        lines.append(f"  ref .{handle} media={media}")
    for media in rec.get("missing") or []:
        lines.append(f"  missing reference media={media}")
    if rec.get("loras"):
        lines.append(f"loras: {', '.join(f'{l.get('name')}:{l.get('strength')}' for l in rec['loras'])}")
    for w in rec.get("warnings") or []:
        lines.append(f"warning: {w.get('kind')} {w.get('detail')}")
    emit(args, rec, "\n".join(lines))


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
        f"lora={info.get('has_lora')} start={info.get('supports_start_frame')} end={info.get('supports_end_frame')} tokens={info.get('addresses_by_token')}"
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


def cmd_post(client, args, kind="agent"):
    slug = need_project(args)
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
    result = client.command(slug, args.name, payload, propose=args.propose)
    emit(args, result, describe_result(result))


SHOT_KEYS = ("name", "bin", "description", "prompt", "negative", "workflow", "references", "length_s", "quarter", "params", "tags")


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
        if status not in ("approved", "done"):
            lines.append(f"  {i + 1}. {name}: {status}, nothing made")
            continue
        if item.get("dropped"):
            lines.append(f"  {i + 1}. {name}: dropped")
            continue
        where = f"bin {item.get('bin_id') or '?'}"
        if item.get("error"):
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
    if args.wait and gen:
        gen = client.wait_generation(slug, gen["id"], poll_s=args.poll, timeout_s=args.timeout)
        result = {**result, "generation": gen}
        human = describe_result(result) + f"\noutputs: {gen.get('outputs')}"
    else:
        human = describe_result(result)
    emit(args, result, human)


def cmd_jobs(client, args):
    jobs = client.jobs(need_project(args))
    emit(args, jobs, "\n".join(f"{j['job']} {j['bin']} {j['status']:8} {j.get('progress') or 0}% takes={j.get('takes')} {j.get('error') or ''}".rstrip() for j in jobs) or "(no jobs)")


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


HANDLERS = {
    "projects": cmd_projects, "create": cmd_create, "workflows": cmd_workflows, "wait": cmd_wait, "connect": cmd_connect, "project": cmd_project, "bin": cmd_bin,
    "inbox": cmd_inbox, "watch": cmd_watch, "post": cmd_post, "blocker": cmd_blocker, "cmd": cmd_cmd, "generate": cmd_generate,
    "propose-shots": cmd_propose_shots,
    "jobs": cmd_jobs, "end": cmd_end, "thumb": cmd_thumb,
    "enhance-requests": cmd_enhance_requests, "enhance-reply": cmd_enhance_reply,
    "sessions": cmd_sessions, "mode": cmd_mode, "history": cmd_history, "recipe": cmd_recipe, "check": cmd_check,
    "frames": cmd_frames, "guide": cmd_guide, "commands": cmd_commands, "upload": cmd_upload, "status": cmd_status,
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
    sub.add_parser("workflows", help="the workflows the server offers, with their kind")
    p = sub.add_parser("wait", help="block until a project has a waiting session")
    p.add_argument("--poll", type=float, default=5.0)
    p.add_argument("--timeout", type=float, default=None)
    p = sub.add_parser("connect", help="connect to the waiting session")
    p.add_argument("--harness", default="claude-code")
    sub.add_parser("project", help="project detail")
    p = sub.add_parser("bin", help="bin detail: recipe, gallery, references with handles, capacity, active job")
    p.add_argument("bin")
    sub.add_parser("inbox", help="messages since the saved cursor; saves the new cursor")
    p = sub.add_parser("watch", help="block until the user writes or decides (one project with --project, else whichever project needs you first); leaves the cursor for inbox to read")
    p.add_argument("--poll", type=float, default=3.0)
    p.add_argument("--timeout", type=float, default=None)
    sub.add_parser("sessions", help="every project with its session status, mode, blocker and pending count")
    p = sub.add_parser("mode", help="set the session mode: auto (loop; the finished candidate is the card), manual (every render proposed first), image_review (every take is a card), video_review (every video take is a card), plan (revise the brief)")
    p.add_argument("mode", choices=["auto", "manual", "image_review", "video_review", "plan"])
    p.add_argument("--requested", action="store_true", help="the user asked for the switch in the feed")
    p = sub.add_parser("history", help="a bin's takes as an experiment log: params, QC marks, and the prompt diff between takes")
    p.add_argument("--bin", required=True)
    p.add_argument("--limit", type=int, default=None, help="newest N")
    p = sub.add_parser("recipe", help="how a media was made: workflow, prompt, references, params")
    p.add_argument("media")
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
    p.add_argument("--quiet", action="store_true", help="work in progress: record it but do not put a card in the feed")
    p.add_argument("--card", action="store_true", help="ask anyway: this change owes a verdict and shows as a card")
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
    p.add_argument("--wait", action="store_true", help="poll until the generation is done or failed")
    p.add_argument("--poll", type=float, default=2.0)
    p.add_argument("--timeout", type=float, default=None)
    sub.add_parser("jobs", help="render jobs known to this server process")
    p = sub.add_parser("end", help="end the session, after posting the wrap-up the next session opens with")
    p.add_argument("--summary", default=None, help="what worked, what is open, which takes are the picks")
    p.add_argument("--attach", action="append", help="media id to attach to the summary (repeatable)")
    p = sub.add_parser("thumb", help="download a thumbnail (or the file) for QC")
    p.add_argument("media")
    p.add_argument("--out", required=True)
    p.add_argument("--size", default="grid", choices=["grid", "detail", "poster", "file"])
    return ap


def main(argv: list[str] | None = None, *, transport=None) -> int:
    args = build_parser().parse_args(argv)
    saved = load_state(args.project) if args.project else {}
    cursor = args.cursor if args.cursor is not None else saved.get("cursor")
    doing = DOING.get(args.command)
    if args.command == "cmd":
        doing = DOING_CMD.get(args.name) or args.name.replace("_", " ")
    # always the agent: no flag puts this CLI in the user's seat, so the private-project gate cannot be talked around
    client = ThmClient(args.server, session=saved.get("session"), cursor=cursor, transport=transport, doing=doing)
    try:
        HANDLERS[args.command](client, args)
    except ThmError as exc:
        print(f"ERROR {exc.status} {exc.code}: {exc.detail}", file=sys.stderr)
        if exc.code == "inbox_behind":
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
