# curate_helpers.py
import gradio as gr
import json
import os
import re
from pathlib import Path
from editor_helpers import _get_kf_gallery_images, _get_vid_gallery_files, _eh_set_selected_image, _eh_set_selected_video
from helpers import (
    _ensure_project, _ensure_seq_defaults, _video_seconds, 
    _fmt_clock, _sequence_effective_length, _rows_with_times,
    get_node_by_id
)

kf_filter_value ="🖼️Keyframes"
ib_filter_value = "🎞️In-betweens"

def generate_curate_galleries(project_dict: dict):
    """
    Parses the project JSON and generates a single flat list of all media
    (images and videos) for a single gallery.
    Returns a gr.update() object for the gallery.
    """
    data = project_dict if isinstance(project_dict, dict) else {}    
    try:
        output_root = data.get("project", {}).get("comfy", {}).get("output_root")
        if not output_root:
            print("Curate Tab Error: 'output_root' not defined in project JSON.")
            return gr.update(value=[], visible=False)
        output_root_path = Path(output_root)
    except Exception as e:
        print(f"Curate Tab Error: Could not read output_root. {e}")
        return gr.update(value=[], visible=False)

    def _to_relative_gallery_data(files_list, root_path):
        """Return absolute POSIX paths for Gradio Gallery while keeping short labels."""
        gallery_data = []
        for p_str in files_list:
            try:
                p_abs = Path(p_str)
                if not p_abs.is_absolute():
                    p_abs = (root_path / p_str).resolve()
                posix_path = str(p_abs).replace("\\", "/")
                gallery_data.append((posix_path, p_abs.name))
            except Exception as e:
                print(f"Error processing path {p_str}: {e}")
                pass
        return gallery_data

    rows = _rows_with_times(data)
    all_media_files = []
    
    if not rows:
        return gr.update(value=[], visible=False) # Hide gallery if no items

    for label, nid in rows:
        node, kind = get_node_by_id(data, nid)
        
        try:
            if kind == "kf":
                image_files = _get_kf_gallery_images(data, nid)
                if image_files:
                    all_media_files.extend(image_files)
                
            elif kind == "vid":
                video_files = _get_vid_gallery_files(data, nid)
                if video_files:
                    all_media_files.extend(video_files)

        except Exception as e:
            print(f"Error building gallery list for {nid}: {e}")

    gallery_data = _to_relative_gallery_data(all_media_files, output_root_path)
    
    return gr.update(value=gallery_data, visible=True)


ROWS_PER_PAGE = 7
IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

def _flatten_items(data, mode: str):
    items = []
    for label, nid in _rows_with_times(data):
        if not nid: continue
        
        node, kind = get_node_by_id(data, nid)
        if not node: continue
        
        if kind == "seq":
            continue
        if mode == kf_filter_value and kind != "kf":
            continue
        if mode == ib_filter_value and kind != "vid":
            continue
        items.append((label, nid))
    return items

def _get_selected(data, nid: str):
    node, kind = get_node_by_id(data, nid)
    if kind == "kf":
        return node.get("selected_image_path") or ""
    if kind == "vid":
        return node.get("selected_video_path") or ""
    return ""

def _options_for(data, nid: str):
    node, kind = get_node_by_id(data, nid)
    if kind == "kf":
        return _get_kf_gallery_images(data, nid) or []
    if kind == "vid":
        return _get_vid_gallery_files(data, nid) or []
    return []

def _render_html(path_str: str, label: str):
    def _front_trunc(name: str, max_len: int = 30) -> str:
        return name if len(name) <= max_len else ("…" + name[-(max_len - 1):])
    
    match = re.search(r"(s\d+).*?((?:kf|ib)\d+)", label)
    simple_label = f"{match.group(1)} {match.group(2)}" if match else label[:15]
    label_html = f"<div class='curate-label'>{simple_label}</div>"

    p = Path(path_str) if path_str else None
    valid = p and p.exists()
    fname = _front_trunc(p.name if valid else "— no selection —")
    if valid:
        # url = f"/gradio_api/file={str(p).replace('\\','/')}"
        path_str = str(p).replace('\\', '/')
        url = f"/gradio_api/file={path_str}"
        if p.suffix.lower() == ".mp4":
            media = f"<video src='{url}' controls></video>"
        else:
            media = f"<img src='{url}' />"
    else:
        media = "<div style='opacity:.4; text-align:center;'>— no media —</div>"
    caption = f"<div class='curate-caption'>{fname}</div>"
    return f"<div class='curate-media'>{label_html}{media}{caption}</div>"

def _wrap_idx(curr_idx: int, step: int, n: int) -> int:
    if n <= 0:
        return 0
    return (curr_idx + step) % n

def _page_slice(nids, page_idx):
    start = page_idx * ROWS_PER_PAGE
    end = start + ROWS_PER_PAGE
    return nids[start:end]

def _render_page(project_dict: dict, mode: str, page_idx: int):
    data = project_dict if isinstance(project_dict, dict) else {}
    nids = _flatten_items(data, mode)
    page_nids = _page_slice(nids, page_idx)
    total = len(nids)
    total_pages = max(1, (total + ROWS_PER_PAGE - 1) // ROWS_PER_PAGE)
    label = f"Page {page_idx+1} of {total_pages} — {total} items"
    rows_html = []
    for label, nid in page_nids:
        sel = _get_selected(data, nid)
        rows_html.append(_render_html(sel, label))
    while len(rows_html) < ROWS_PER_PAGE:
        rows_html.append("<div style='opacity:.4'>—</div>")
    return nids, label, rows_html

def _set_next(project_dict: dict, mode: str, page_idx: int, row_offset: int, step: int):
    """Advance selection for the row at row_offset by step (-1 or +1)."""
    data = project_dict if isinstance(project_dict, dict) else {}
    nids = _flatten_items(data, mode)
    idx = page_idx * ROWS_PER_PAGE + row_offset
    if idx >= len(nids):
        return data, gr.update()
    
    label, nid = nids[idx]
    node, kind = get_node_by_id(data, nid)
    
    opts = _options_for(data, nid)
    if not opts:
        return data, gr.update()

    current = _get_selected(data, nid)
    try:
        curr_idx = opts.index(current) if current in opts else -1
    except ValueError:
        curr_idx = -1
    next_idx = _wrap_idx(curr_idx, step, len(opts))
    target = str(opts[next_idx])

    if kind == "kf":
        new_dict, _, _ = _eh_set_selected_image(data, nid, target)
        data = new_dict
    elif kind == "vid":
        new_dict, _ = _eh_set_selected_video(data, nid, target)
        data = new_dict

    row_html = _render_html(target, label)
    return data, gr.update(value=row_html)

def curate_refresh(project_dict: dict, mode: str):
    _nids, label, rows = _render_page(project_dict, mode, 0)
    updates = [gr.update(value=f"<div style='text-align:center'>{label}</div>")] + [gr.update(value=h) for h in rows]
    return [0] + updates

def _paginate(project_dict: dict, mode: str, page_idx: int, delta: int):
    data = project_dict if isinstance(project_dict, dict) else {}
    nids = _flatten_items(data, mode)
    total_pages = max(1, (len(nids) + ROWS_PER_PAGE - 1) // ROWS_PER_PAGE)
    new_idx = max(0, min(total_pages - 1, page_idx + delta))
    _nids, label, rows = _render_page(project_dict, mode, new_idx)
    return [new_idx, gr.update(value=f"<div style='text-align:center'>{label}</div>")] + [gr.update(value=h) for h in rows]

def build_curate_tab(preview_code):
    """
    Curate viewport: 7 rows per page, browser scroll within page, Prev/Next Page to paginate.
    Each row always editable: Back/Next wrap within that item's options and immediately persist.
    """
    # ---------- UI ----------
    with gr.Column():
        gr.HTML("""
        <style>
        .curate-row{
            display:flex !important;
            flex-wrap:nowrap !important;
            align-items:stretch !important;
            justify-content:center !important;
            column-gap:12px;
        }
        .arrow-col{
            flex:0 0 auto !important;
            width:clamp(22px, 2.2vw, 32px) !important;
            max-width:clamp(22px, 2.2vw, 32px) !important;
        }
        .arrow-col .gr-button, .arrow-col button{
            width:100% !important;
            min-width:0 !important;
            padding:0 !important;
            margin:0 !important;
            height:100% !important;
            line-height:1 !important;
            white-space:nowrap !important;
            font-size:clamp(15px,1.8vw,20px) !important;
        }
        .media-col{
            flex:1 1 auto !important;
            min-width:clamp(260px,55vw,900px);
            max-width:min(100%,1200px);
            margin:0 auto;
        }
        .curate-media{display:block; position: relative;}
        .curate-media img,.curate-media video{display:block;max-width:100%;height:auto;}
        .curate-label {
            position: absolute;
            top: 6px;
            left: 6px;
            background-color: rgba(0, 0, 0, 0.7);
            color: white;
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 13px;
            font-weight: bold;
            z-index: 10;
        }
        .curate-caption { min-height: 1.2em; margin-top: 6px; }
        .curate-caption{
            display:block;
            margin-top:6px;
            text-align:center;
            font-size:12px;
            color:var(--body-text-color,#ddd);
            white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
        }
        </style>
        """)

        filter_mode = gr.Radio([kf_filter_value, ib_filter_value, "All"], value=kf_filter_value, label="Filter")

        row_html = []
        row_prev = []
        row_next = []
        for i in range(ROWS_PER_PAGE):
            with gr.Row():
                with gr.Row(elem_classes=["curate-row"]):
                    with gr.Column(scale=0, elem_classes=["arrow-col"]):
                        btn_prev = gr.Button("‹", elem_classes=["arrow-btn"])
                    with gr.Column(scale=1, elem_classes=["media-col"]):
                        html = gr.HTML("<div style='opacity:.6'>—</div>", elem_id=f"curate_row_{i}")
                    with gr.Column(scale=0, elem_classes=["arrow-col"]):
                        btn_next = gr.Button("›", elem_classes=["arrow-btn"])

            row_prev.append(btn_prev)
            row_next.append(btn_next)
            row_html.append(html)

        with gr.Row():
            prev_page = gr.Button("‹ Prev", variant="secondary")
            next_page = gr.Button("Next ›", variant="secondary")
        page_text = gr.HTML("<div style='text-align:center'>Page 1 of 1 — 0 items</div>")

        page_idx_state = gr.State(value=0)


    prev_page.click(
        fn=lambda pre, mode, idx: _paginate(pre, mode, idx, -1),
        inputs=[preview_code, filter_mode, page_idx_state],
        outputs=[page_idx_state, page_text] + row_html
    )
    next_page.click(
        fn=lambda pre, mode, idx: _paginate(pre, mode, idx, +1),
        inputs=[preview_code, filter_mode, page_idx_state],
        outputs=[page_idx_state, page_text] + row_html
    )

    for i in range(ROWS_PER_PAGE):
        row_prev[i].click(
            fn=lambda pre, mode, idx, _i=i: _set_next(pre, mode, idx, _i, -1),
            inputs=[preview_code, filter_mode, page_idx_state],
            outputs=[preview_code, row_html[i]]
        )
        row_next[i].click(
            fn=lambda pre, mode, idx, _i=i: _set_next(pre, mode, idx, _i, +1),
            inputs=[preview_code, filter_mode, page_idx_state],
            outputs=[preview_code, row_html[i]]
        )

    filter_mode.change(
        fn=curate_refresh,
        inputs=[preview_code, filter_mode],
        outputs=[page_idx_state, page_text] + row_html
    )

    return filter_mode, page_text, row_html