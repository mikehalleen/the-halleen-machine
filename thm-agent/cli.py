#!/usr/bin/env python3
"""CLI for thm-agent — project JSON + ComfyUI generation client. Run from repo root."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

_AGENT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _AGENT_DIR.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

import builder  # noqa: E402
import pipeline  # noqa: E402
from client import discover, runner, selection  # noqa: E402
from client.config_helpers import resolve_agent_python  # noqa: E402
from client.workspace import (
    attach_workspace_paths,
    add_preview_image,
    build_preview_gallery,
    clear_preview_dir,
    open_gallery_in_browser,
    open_with_default_app,
    preview_dir_for_project,
    reveal_in_explorer,
    update_gallery_session,
    group_key_from_filename,
)


def _print_json(obj) -> None:
    text = json.dumps(obj, indent=2, ensure_ascii=False)
    try:
        print(text)
    except UnicodeEncodeError:
        print(json.dumps(obj, indent=2, ensure_ascii=True))


def cmd_validate(path: Path, *, as_json: bool) -> int:
    try:
        data = builder.load_project(path)
    except Exception as e:
        if as_json:
            _print_json({"ok": False, "error": str(e)})
        else:
            print(f"ERROR: cannot load {path}: {e}")
        return 1

    issues = builder.validate_project(data)
    ok, save_msg = builder.validate_before_save(data, str(path))
    if not ok:
        issues.append(save_msg)

    if as_json:
        _print_json({"ok": not issues, "path": str(path), "issues": issues})
        return 0 if not issues else 1

    if issues:
        print(f"INVALID: {path}")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    print(f"OK: {path}")
    return 0


def cmd_summarize(path: Path) -> int:
    try:
        data = builder.load_project(path)
    except Exception as e:
        print(f"ERROR: cannot load {path}: {e}")
        return 1
    print(builder.summarize_project(data), end="")
    return 0


def cmd_comfy_status(*, as_json: bool, api_base: str | None) -> int:
    result = discover.comfy_status(api_base)
    if as_json:
        _print_json(result)
    else:
        print(result["message"])
    return 0 if result["online"] else 1


def cmd_discover(kind: str, *, as_json: bool) -> int:
    dispatch = {
        "models": discover.list_checkpoints,
        "loras": discover.list_loras,
        "workflows": discover.list_workflows,
        "projects": discover.list_projects,
    }
    items = dispatch[kind]()
    if as_json:
        _print_json({"kind": kind, "items": items})
    else:
        for item in items:
            print(item)
    return 0


def cmd_images_list(path: Path, seq_id: str, kf_id: str, *, as_json: bool, previews: bool) -> int:
    try:
        data = builder.load_project(path)
    except Exception as e:
        if as_json:
            _print_json({"ok": False, "error": str(e)})
        else:
            print(f"ERROR: {e}")
        return 1
    images = discover.list_keyframe_images(data, seq_id, kf_id, include_previews=previews)
    if as_json:
        _print_json({"seq_id": seq_id, "kf_id": kf_id, "images": images})
    else:
        for img in images:
            print(img)
    return 0


def cmd_generate_keyframe(
    path: Path,
    seq_id: str,
    kf_id: str,
    *,
    seed: int | None,
    variants: int,
    as_json: bool,
) -> int:
    try:
        data = builder.load_project(path)
    except Exception as e:
        if as_json:
            _print_json({"ok": False, "error": str(e)})
        else:
            print(f"ERROR: {e}")
        return 1

    project_name = data.get("project", {}).get("name", "project")
    label = f"keyframes/{seq_id}/{kf_id}"
    results = []
    count = max(1, variants)
    for i in range(count):
        run_seed = seed + i if seed is not None and count > 1 else seed
        result = runner.run_keyframe(data, seq_id, kf_id, seed=run_seed)
        rd = asdict(result)
        attach_workspace_paths(rd, project_name=project_name, label=label, seed=run_seed)
        results.append(rd)

    payload = {
        "ok": any(r["success"] for r in results),
        "variants": results,
        "main_path": results[-1]["main_path"] if results else None,
        "workspace_path": results[-1].get("workspace_path") if results else None,
    }
    if as_json:
        _print_json(payload)
    else:
        for r in results:
            print(f"success={r['success']} path={r['main_path']}")
            if r.get("workspace_path"):
                print(f"  workspace={r['workspace_path']}")
            if not r["success"]:
                print(r["log"][-2000:])
    return 0 if payload["ok"] else 1


def cmd_generate_video(
    path: Path,
    seq_id: str,
    vid_id: str,
    *,
    seed: int | None,
    as_json: bool,
) -> int:
    try:
        data = builder.load_project(path)
    except Exception as e:
        if as_json:
            _print_json({"ok": False, "error": str(e)})
        else:
            print(f"ERROR: {e}")
        return 1

    prereq = runner.validate_video_prerequisites(data, seq_id, vid_id)
    if prereq:
        if as_json:
            _print_json({"ok": False, "error": "missing selected_image_path", "issues": prereq})
        else:
            print("ERROR: video generation requires selected keyframe images:")
            for issue in prereq:
                print(f"  - {issue}")
        return 1

    project_name = data.get("project", {}).get("name", "project")
    result = runner.run_video(data, seq_id, vid_id, seed=seed)
    payload = {"ok": result.success, **asdict(result)}
    attach_workspace_paths(
        payload, project_name=project_name, label=f"videos/{seq_id}/{vid_id}", seed=seed
    )
    if as_json:
        _print_json(payload)
    else:
        print(f"success={result.success} path={result.main_path}")
        if payload.get("workspace_path"):
            print(f"  workspace={payload['workspace_path']}")
        if not result.success:
            print(result.log[-2000:])
    return 0 if result.success else 1


def cmd_generate_asset(
    path: Path,
    asset_type: str,
    asset_id: str,
    *,
    seed: int | None,
    workflow: str | None,
    layout_override: str | None,
    as_json: bool,
) -> int:
    try:
        data = builder.load_project(path)
    except Exception as e:
        if as_json:
            _print_json({"ok": False, "error": str(e)})
        else:
            print(f"ERROR: {e}")
        return 1

    project_name = data.get("project", {}).get("name", "project")
    result = runner.run_asset(
        data,
        asset_type,
        asset_id,
        session_workflow=workflow,
        seed=seed,
        layout_override=layout_override,
    )
    payload = {"ok": result.success, **asdict(result)}
    attach_workspace_paths(
        payload,
        project_name=project_name,
        label=f"assets/{asset_type}/{asset_id}",
        seed=seed,
    )
    if as_json:
        _print_json(payload)
    else:
        print(f"success={result.success} path={result.main_path}")
        if payload.get("workspace_path"):
            print(f"  workspace={payload['workspace_path']}")
        if not result.success:
            print(result.log[-2000:])
    return 0 if result.success else 1


def cmd_open(path: Path, *, as_json: bool) -> int:
    try:
        opened = open_with_default_app(path)
    except FileNotFoundError as e:
        if as_json:
            _print_json({"ok": False, "error": str(e)})
        else:
            print(f"ERROR: {e}")
        return 1
    msg = {"ok": True, "path": opened}
    if as_json:
        _print_json(msg)
    else:
        print(f"OK: opened with default app: {opened}")
    return 0


def cmd_gallery(
    project_name: str,
    *,
    open_browser: bool,
    clear: bool,
    src: Path | None,
    name: str | None,
    note: str | None,
    pending: str | None,
    change: str | None,
    context: str | None,
    group: str | None,
    group_note: str | None,
    group_pending: str | None,
    as_json: bool,
) -> int:
    try:
        if clear:
            clear_preview_dir(project_name)
        if src is not None:
            if not name:
                if as_json:
                    _print_json({"ok": False, "error": "--name required with --src"})
                else:
                    print("ERROR: --name required with --src")
                return 1
            copied = add_preview_image(project_name, src, name, note=note)
            if not copied:
                if as_json:
                    _print_json({"ok": False, "error": f"Source not found: {src}"})
                else:
                    print(f"ERROR: source not found: {src}")
                return 1
        elif name and note is not None:
            update_gallery_session(
                project_name,
                item_filename=name,
                item_note=note,
            )
        effective_group = group
        if effective_group is None and name and (
            group_note is not None or group_pending is not None
        ):
            effective_group = group_key_from_filename(name)
        index_path = build_preview_gallery(
            project_name,
            pending=pending,
            change=change,
            context=context,
            group=effective_group,
            group_note=group_note,
            group_pending=group_pending,
        )
        preview_dir = preview_dir_for_project(project_name)
        opened = None
        if open_browser:
            opened = open_gallery_in_browser(index_path, project_name=project_name)
        msg = {
            "ok": True,
            "project": project_name,
            "preview_dir": str(preview_dir.resolve()),
            "index_path": index_path,
            "opened_in_browser": opened,
            "cleared": clear,
        }
        if src is not None:
            msg["copied"] = copied
        if as_json:
            _print_json(msg)
        else:
            print(f"OK: gallery at {index_path}")
            if src is not None:
                print(f"  copied {name} -> {copied}")
            if opened:
                print(f"  opened in browser: {opened}")
        return 0
    except Exception as e:
        if as_json:
            _print_json({"ok": False, "error": str(e)})
        else:
            print(f"ERROR: {e}")
        return 1


def cmd_reveal(path: Path, *, select_file: bool, as_json: bool) -> int:
    try:
        opened = reveal_in_explorer(path, select_file=select_file)
    except FileNotFoundError as e:
        if as_json:
            _print_json({"ok": False, "error": str(e)})
        else:
            print(f"ERROR: {e}")
        return 1
    msg = {"ok": True, "path": opened, "select_file": select_file}
    if as_json:
        _print_json(msg)
    else:
        print(f"OK: opened Explorer for {opened}")
    return 0


def cmd_clone_from_host(
    source: Path,
    name: str,
    *,
    dest: Path | None,
    approve: bool,
    as_json: bool,
) -> int:
    if not approve:
        msg = "Refused: --approve required (user must opt in to clone from host project)."
        if as_json:
            _print_json({"ok": False, "error": msg})
        else:
            print(f"ERROR: {msg}")
        return 1
    if not source.is_file():
        msg = f"Host project not found: {source}"
        if as_json:
            _print_json({"ok": False, "error": msg})
        else:
            print(f"ERROR: {msg}")
        return 1
    try:
        _data, written = builder.clone_project_from_host(source, name, dest_path=dest)
    except Exception as e:
        if as_json:
            _print_json({"ok": False, "error": str(e)})
        else:
            print(f"ERROR: {e}")
        return 1
    payload = {
        "ok": True,
        "source": str(source.resolve()),
        "dest": str(written.resolve()),
        "name": name,
        "host_unmodified": True,
    }
    if as_json:
        _print_json(payload)
    else:
        print(f"OK: cloned globals from {source} -> {written}")
        print("Host project was not modified.")
    return 0


def cmd_pipeline_keyframes(
    path: Path,
    *,
    variants: int,
    force: bool,
    as_json: bool,
) -> int:
    ok = pipeline.phase_keyframes_generate(path, max_variants=variants, force=force)
    cp = pipeline.load_checkpoint(path)
    payload = {"ok": ok, "checkpoint": cp.to_dict(), "log": str(pipeline.log_path(path))}
    if as_json:
        _print_json(payload)
    else:
        print(f"Keyframes phase: {'OK' if ok else 'FAILED'}")
        print(f"Checkpoint: {pipeline.checkpoint_path(path)}")
    return 0 if ok else 1


def cmd_pipeline_record_qc(
    path: Path,
    seq_id: str,
    kf_id: str,
    image: str,
    *,
    rationale: str,
    as_json: bool,
) -> int:
    beat = pipeline.record_vision_selection(
        path, seq_id, kf_id, image, rationale=rationale,
    )
    payload = {
        "ok": True,
        "seq": seq_id,
        "kf": kf_id,
        "selected_path": beat.selected_path,
        "qc_method": beat.qc_method,
    }
    if as_json:
        _print_json(payload)
    else:
        print(f"OK: recorded vision QC for {seq_id}/{kf_id}")
    return 0


def cmd_pipeline_apply(path: Path, *, as_json: bool) -> int:
    applied = pipeline.apply_checkpoint_selections(path)
    payload = {"ok": True, "applied": applied}
    if as_json:
        _print_json(payload)
    else:
        print(f"OK: applied {len(applied)} selection(s)")
    return 0


def cmd_pipeline_status(path: Path, *, as_json: bool) -> int:
    cp = pipeline.load_checkpoint(path)
    data = pipeline.load_project_json(path)
    kfs = pipeline.keyframe_list(data)
    pending = []
    for seq, kf, _ in kfs:
        if not pipeline.keyframe_has_selection(data, seq, kf):
            pending.append(f"{seq}/{kf}")
    payload = {
        "ok": True,
        "checkpoint": cp.to_dict(),
        "pending_keyframes": pending,
        "all_selected": pipeline.all_beats_selected(path),
        "log": str(pipeline.log_path(path)),
    }
    if as_json:
        _print_json(payload)
    else:
        print(f"Phase: {cp.phase}")
        print(f"Pending keyframes: {len(pending)}")
        print(f"All selected: {payload['all_selected']}")
    return 0


def cmd_select_keyframe(path: Path, seq_id: str, kf_id: str, image_path: str, *, as_json: bool) -> int:
    try:
        selection.set_selected_keyframe_image(path, seq_id, kf_id, image_path)
    except Exception as e:
        if as_json:
            _print_json({"ok": False, "error": str(e)})
        else:
            print(f"ERROR: {e}")
        return 1
    msg = {
        "ok": True,
        "path": str(path),
        "seq_id": seq_id,
        "kf_id": kf_id,
        "selected_image_path": image_path,
        "reload_browser": "Reload Gradio if open to avoid write clashes.",
    }
    if as_json:
        _print_json(msg)
    else:
        print(f"OK: selected {image_path}")
        print(msg["reload_browser"])
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        resolve_agent_python()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    parser = argparse.ArgumentParser(description="THM agent client — JSON + generation")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="JSON output for agent parsing")
    sub = parser.add_subparsers(dest="command", required=True)

    p_val = sub.add_parser("validate", parents=[common], help="Validate project JSON")
    p_val.add_argument("path", type=Path)

    p_sum = sub.add_parser("summarize", parents=[common], help="Storyboard summary")
    p_sum.add_argument("path", type=Path)

    sub.add_parser("comfy-status", parents=[common], help="Check ComfyUI API reachability")

    p_disc = sub.add_parser("discover", parents=[common], help="List local resources")
    p_disc.add_argument(
        "kind",
        choices=["models", "loras", "workflows", "projects"],
    )

    p_img = sub.add_parser("images", parents=[common], help="List keyframe images")
    p_img_sub = p_img.add_subparsers(dest="images_cmd", required=True)
    p_img_list = p_img_sub.add_parser("list", parents=[common])
    p_img_list.add_argument("--project", type=Path, required=True)
    p_img_list.add_argument("--seq", required=True)
    p_img_list.add_argument("--kf", required=True)
    p_img_list.add_argument("--previews", action="store_true")

    p_gen = sub.add_parser("generate", parents=[common], help="Run generation")
    p_gen_sub = p_gen.add_subparsers(dest="gen_cmd", required=True)

    p_kf = p_gen_sub.add_parser("keyframe", parents=[common])
    p_kf.add_argument("--project", type=Path, required=True)
    p_kf.add_argument("--seq", required=True)
    p_kf.add_argument("--kf", required=True)
    p_kf.add_argument("--seed", type=int)
    p_kf.add_argument("--variants", type=int, default=1)

    p_vid = p_gen_sub.add_parser("video", parents=[common])
    p_vid.add_argument("--project", type=Path, required=True)
    p_vid.add_argument("--seq", required=True)
    p_vid.add_argument("--vid", required=True)
    p_vid.add_argument("--seed", type=int)

    p_ast = p_gen_sub.add_parser("asset", parents=[common])
    p_ast.add_argument("--project", type=Path, required=True)
    p_ast.add_argument("--type", required=True, choices=["character", "setting", "style"])
    p_ast.add_argument("--id", required=True, dest="asset_id")
    p_ast.add_argument("--seed", type=int)
    p_ast.add_argument("--workflow")
    p_ast.add_argument(
        "--layout-override",
        dest="layout_override",
        help="Override default asset-test layout (poses, shelf angles, etc.)",
    )

    p_open = sub.add_parser("open", parents=[common], help="Open file with default app (Photos, etc.)")
    p_open.add_argument("path", type=Path, help="Image or video file to open")

    p_reveal = sub.add_parser("reveal", parents=[common], help="Open folder in Explorer (Windows)")
    p_reveal.add_argument("path", type=Path, help="File or directory to reveal")
    p_reveal.add_argument(
        "--select",
        action="store_true",
        help="Highlight file in Explorer (/select on Windows)",
    )

    p_gallery = sub.add_parser(
        "gallery",
        parents=[common],
        help="Build {workspace}/{project}-files/previews/index.html and optionally open in browser",
    )
    p_gallery.add_argument("--project", required=True, help="Project name (not JSON path)")
    p_gallery.add_argument(
        "--open",
        action="store_true",
        help="Open index.html in the default browser after building",
    )
    p_gallery.add_argument(
        "--clear",
        action="store_true",
        help="User-only: wipe preview media and session.json (agent must not use — galleries are additive)",
    )
    p_gallery.add_argument(
        "--src",
        type=Path,
        help="Copy a file into the preview folder before rebuilding",
    )
    p_gallery.add_argument(
        "--name",
        help="Short destination filename for --src (e.g. creator-A.png)",
    )
    p_gallery.add_argument(
        "--note",
        help="Hint for this item (--with --src --name) or set via session flags",
    )
    p_gallery.add_argument(
        "--pending",
        help="Session-level: open decision the user should make",
    )
    p_gallery.add_argument(
        "--change",
        help="Session-level: what changed in the latest batch (memory aid)",
    )
    p_gallery.add_argument(
        "--context",
        help="Session-level: broader context for this review session",
    )
    p_gallery.add_argument(
        "--group",
        help="Group key prefix for --group-note / --group-pending (e.g. video, keyframe)",
    )
    p_gallery.add_argument(
        "--group-note",
        dest="group_note",
        help="Note applying to all items in --group",
    )
    p_gallery.add_argument(
        "--group-pending",
        dest="group_pending",
        help="Pending decision for the --group section",
    )

    p_sel = sub.add_parser("select", parents=[common], help="Persist selection")
    p_sel_sub = p_sel.add_subparsers(dest="sel_cmd", required=True)
    p_sel_kf = p_sel_sub.add_parser("keyframe", parents=[common])
    p_sel_kf.add_argument("--project", type=Path, required=True)
    p_sel_kf.add_argument("--seq", required=True)
    p_sel_kf.add_argument("--kf", required=True)
    p_sel_kf.add_argument("--image", required=True)

    p_clone = sub.add_parser(
        "clone-from-host",
        parents=[common],
        help="New project JSON with host globals; host file read-only",
    )
    p_clone.add_argument("--source", type=Path, required=True, help="Host project JSON (read-only)")
    p_clone.add_argument("--name", required=True, help="New project name")
    p_clone.add_argument("--dest", type=Path, help="Destination JSON path (default: workspace/{slug}.json)")
    p_clone.add_argument(
        "--approve",
        action="store_true",
        required=True,
        help="Required: user opted in to clone from host",
    )

    p_pipe = sub.add_parser("pipeline", parents=[common], help="Long-run pipeline + checkpoints")
    p_pipe_sub = p_pipe.add_subparsers(dest="pipe_cmd", required=True)

    p_pkf = p_pipe_sub.add_parser("keyframes", parents=[common], help="Generate KF variants to checkpoint")
    p_pkf.add_argument("--project", type=Path, required=True)
    p_pkf.add_argument("--variants", type=int, default=5)
    p_pkf.add_argument("--force", action="store_true", help="Regenerate even if already selected")

    p_pqc = p_pipe_sub.add_parser("record-qc", parents=[common], help="Record vision QC selection")
    p_pqc.add_argument("--project", type=Path, required=True)
    p_pqc.add_argument("--seq", required=True)
    p_pqc.add_argument("--kf", required=True)
    p_pqc.add_argument("--image", required=True)
    p_pqc.add_argument("--rationale", default="", help="Vision QC rationale for log")

    p_papply = p_pipe_sub.add_parser("apply-selections", parents=[common])
    p_papply.add_argument("--project", type=Path, required=True)

    p_pstat = p_pipe_sub.add_parser("status", parents=[common])
    p_pstat.add_argument("--project", type=Path, required=True)

    args = parser.parse_args(argv)

    if args.command == "validate":
        return cmd_validate(args.path, as_json=args.json)
    if args.command == "summarize":
        return cmd_summarize(args.path)
    if args.command == "comfy-status":
        return cmd_comfy_status(as_json=args.json, api_base=getattr(args, "api_base", None))
    if args.command == "discover":
        return cmd_discover(args.kind, as_json=args.json)
    if args.command == "images" and args.images_cmd == "list":
        return cmd_images_list(
            args.project, args.seq, args.kf, as_json=args.json, previews=args.previews
        )
    if args.command == "generate":
        if args.gen_cmd == "keyframe":
            return cmd_generate_keyframe(
                args.project, args.seq, args.kf,
                seed=args.seed, variants=args.variants, as_json=args.json,
            )
        if args.gen_cmd == "video":
            return cmd_generate_video(
                args.project, args.seq, args.vid, seed=args.seed, as_json=args.json,
            )
        if args.gen_cmd == "asset":
            return cmd_generate_asset(
                args.project,
                args.type,
                args.asset_id,
                seed=args.seed,
                workflow=args.workflow,
                layout_override=args.layout_override,
                as_json=args.json,
            )
    if args.command == "open":
        return cmd_open(args.path, as_json=args.json)
    if args.command == "reveal":
        return cmd_reveal(args.path, select_file=args.select, as_json=args.json)
    if args.command == "gallery":
        return cmd_gallery(
            args.project,
            open_browser=args.open,
            clear=args.clear,
            src=args.src,
            name=args.name,
            note=args.note,
            pending=args.pending,
            change=args.change,
            context=args.context,
            group=args.group,
            group_note=args.group_note,
            group_pending=args.group_pending,
            as_json=args.json,
        )
    if args.command == "select" and args.sel_cmd == "keyframe":
        return cmd_select_keyframe(
            args.project, args.seq, args.kf, args.image, as_json=args.json,
        )
    if args.command == "clone-from-host":
        return cmd_clone_from_host(
            args.source, args.name, dest=args.dest, approve=args.approve, as_json=args.json,
        )
    if args.command == "pipeline":
        if args.pipe_cmd == "keyframes":
            return cmd_pipeline_keyframes(
                args.project, variants=args.variants, force=args.force, as_json=args.json,
            )
        if args.pipe_cmd == "record-qc":
            return cmd_pipeline_record_qc(
                args.project, args.seq, args.kf, args.image,
                rationale=args.rationale, as_json=args.json,
            )
        if args.pipe_cmd == "apply-selections":
            return cmd_pipeline_apply(args.project, as_json=args.json)
        if args.pipe_cmd == "status":
            return cmd_pipeline_status(args.project, as_json=args.json)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
