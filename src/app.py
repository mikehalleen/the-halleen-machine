# app.py
import os, json, time, threading, hashlib
from datetime import datetime
import gradio as gr
from gradio.themes.utils import colors
from pathlib import Path
from typing import Any
from form_manager import ProjectFormRegistry


from helpers import (
    APP_TITLE, ensure_settings, DEFAULT_PROJECT, normalize_project_shape, _deep_copy,
    cb_create_new_project, cb_open_file,
    cb_list_json_files, cb_list_model_files, cb_list_workflow_files, cb_list_pose_files, cb_master_refresh,
    cb_save_project, cb_save_as, cb_save_settings, flush_gradio_cache, 
    refresh_pose_components, DUR_CHOICES, get_project_poses_dir, _set_by_path,
    load_project_complete, save_to_project_folder,
    WORKFLOWS_DIR, project_default_workflow_filename,
    effective_default_workflow_filename,
    image_family_label_to_json, image_family_json_to_label, DEFAULT_PROJECT_WORKFLOW_FILENAME,
    DEFAULT_PROJECT_WORKFLOW_FILENAME,
    DEFAULT_VIDEO_WORKFLOW_FILENAME,
    video_workflow_path_to_dropdown,
    video_workflow_dropdown_to_path,
    IMAGE_MODEL_FAMILY_DEFAULT, is_default_image_family, is_custom_image_family,
    VIDEO_MODEL_FAMILY_DEFAULT, is_default_video_family, is_custom_video_family,
    video_model_family, video_family_label_to_json, video_family_json_to_label,
    effective_video_workflow_filename, stored_video_workflow_filename,
    migrate_video_to_default_workflow, should_migrate_video_on_family_change,
    cb_refresh_video_workflow_dropdown, resolve_project_video_workflow,
    migrate_keyframes_to_custom_reference_bindings,
    should_migrate_keyframes_to_custom_bindings,
    first_outline_node_id,
    outline_node_exists,
    image_model_family,
)
from workflow_capabilities import (
    project_negative_visibility,
    scan_workflow_file,
    scan_video_workflow_file,
    video_generation_defaults_visibility,
    video_workflow_name_from_project,
)

from curate_helpers import build_curate_tab, curate_refresh
from editor_helpers import build_editor_tab, _eh_node_selected, _project_len_text
from assets_helpers import build_assets_tab, broadcast_lora_choices
from helpers import get_project_poses_dir, get_pose_gallery_list
from run_helpers import build_run_tab, check_comfyui_status


from single_gen_helpers import (
    handle_style_asset_test,
    handle_setting_test,
    recall_project_globals,
    save_style_to_project,
    get_style_test_images,
    list_style_test_options,
    run_style_preview_click,
    sync_style_test_scene_dropdown,
)

settings = ensure_settings()
features = settings.get("features", {})
settings_json_init = json.dumps(settings, indent=2, ensure_ascii=False)
# Initialize with an empty project, not the default, to force loading a file.
project_json_init = {}

SAMPLER_CHOICES = ['dpmpp_2m_sde', 'dpmpp_2m', 'dpmpp_sde', 'euler', 'euler_ancestral', 'lms', 'heun', 'dpm_fast']
SCHEDULER_CHOICES = ['karras', 'normal', 'simple', 'exponential']


def _check_config_on_startup():
    """
    Validate config.toml on startup.
    Exits if missing/invalid.
    """
    from pathlib import Path
    import sys
    
    config_path = Path("config.toml")
    
    if not config_path.exists():
        print()
        print("=" * 70)
        print("  config.toml not found!")
        print()
        print("  To fix, run:  python setup.py or copy config.toml.example to config.toml and edit manually")
        print("=" * 70)
        print()
        sys.exit(1)
    
    # Try to parse
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        try:
            import tomli as tomllib
        except ImportError:
            return
    
    try:
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
    except Exception as e:
        print()
        print("=" * 70)
        print(f"  config.toml has syntax errors: {e}")
        print()
        print("  To fix, run:  python setup.py or copy config.toml.example to config.toml and edit manually")
        print("=" * 70)
        print()
        sys.exit(1)
    
    # Check required fields
    errors = []
    if not data.get("comfyui", {}).get("output_root"):
        errors.append("[comfyui] output_root")
    if not data.get("paths", {}).get("models"):
        errors.append("[paths] models")
    
    if errors:
        print()
        print("=" * 70)
        print("  config.toml is incomplete - missing:")
        for e in errors:
            print(f"     - {e}")
        print()
        print("  To fix, run:  python setup.py or copy config.toml.example to config.toml and edit manually")
        print("=" * 70)
        print()
        sys.exit(1)



def _ts_name():
    return datetime.now().strftime("Untitled-%Y%m%d-%H%M%S")



def _manual_set(pj, key, val):
    import json
    if isinstance(pj, dict):
            data = pj
    else:
            try: data = json.loads(pj)
            except: data = {}
    try: val = int(float(val))
    except: pass
    _set_by_path(data, key, val)
    return data

_autosave_lock = threading.Lock()
_autosave_last_time = 0
_AUTOSAVE_DEBOUNCE_SEC = 0.5

def _file_fingerprint(file_path):
    """sha256 of the file's raw bytes, or "" if it can't be read.
    Used to detect another human editor (PC vs mobile, etc.) writing to the
    same project file — content-based rather than mtime-based so two
    different devices' clocks can't throw it off."""
    try:
        return hashlib.sha256(Path(file_path).read_bytes()).hexdigest()
    except OSError:
        return ""

def _trigger_autosave(file_path, project_data, settings_str, autosave_on=True, last_known_fingerprint=""):
    """Helper to trigger the existing save logic with debouncing."""
    global _autosave_last_time

    if not autosave_on:
        print(f"[AUTOSAVE] Skipped - autosave is off")
        return gr.update(), gr.update(), gr.update()

    # Guard against incomplete inputs during initialization
    if not file_path or not project_data:
        print(f"[AUTOSAVE] Skipped - no file/data")
        return gr.update(), gr.update(), gr.update()

    # Belt-and-suspenders against the Autosave checkbox being stale on the
    # client right after a mode switch (the corrected value hasn't
    # round-tripped back from the server yet when this fires): never let an
    # automatic/implicit trigger overwrite an agent-managed file. Only the
    # explicit Save button (which doesn't go through this function) can.
    if isinstance(project_data, dict) and project_data.get("project", {}).get("active_writer") == "agent":
        print(f"[AUTOSAVE] Skipped - project is agent-managed (active_writer=agent)")
        return gr.update(), gr.update(), gr.update()

    # Another human editor (a different device/session) may have written to
    # this file since we last synced with disk — check before blindly
    # overwriting. A file's first-ever load has no prior fingerprint yet, so
    # an empty last_known_fingerprint is never treated as a conflict.
    current_fp = _file_fingerprint(file_path)
    if current_fp and last_known_fingerprint and current_fp != last_known_fingerprint:
        print(f"[AUTOSAVE] Skipped - another editor has written since last sync, disabling autosave")
        return gr.update(value=False), current_fp, "another editor is active — autosave off"

    with _autosave_lock:
        now = time.time()
        if now - _autosave_last_time < _AUTOSAVE_DEBOUNCE_SEC:
            print(f"[AUTOSAVE] Debounced")
            return gr.update(), gr.update(), gr.update()
        _autosave_last_time = now

    print(f"[AUTOSAVE] Saving on tab switch: {file_path}")
    cb_save_project(file_path, project_data, settings_str)
    return gr.update(), _file_fingerprint(file_path), gr.update()

def _update_project_name_header(name: str):
    """Formats the project name for the header markdown."""
    if name and name.strip():
        return gr.Markdown(f"**Media Path:** `{name.strip()}`")
    return gr.Markdown("")


form_field_outputs = [] # We will populate this later in the file

def _conditionally_apply_update(result_data: dict, current_file_path: str, current_json: str):
    """
    Applies an update from a generator only if the project path matches.
    """
    if not isinstance(result_data, dict):
        return gr.update() # No change

    result_json = result_data.get("final_json")
    path_at_start = result_data.get("source_path")

    if not result_json or not path_at_start:
        return gr.update() # Not a final update, ignore

    if path_at_start == current_file_path:
        print(f"[UPDATE] Applying generation results to {current_file_path}.")
        return result_json # Apply update
    else:
        print(f"[UPDATE] Discarding stale generation results from {path_at_start} (current is {current_file_path}).")
        return current_json # Discard update (return current state)

def _dur_to_choice(val) -> str:
    try:
        if isinstance(val, (int, float)):
            s = str(int(round(float(val))))
        else:
            s = str(val).strip()
            if s.replace(".", "", 1).isdigit():
                s = str(int(round(float(s))))
    except Exception:
        s = "5"
    if s not in DUR_CHOICES:
        s = "5"
    return s

# ---- Custom Theme & CSS Definition ----
theme = gr.themes.Default()
custom_css = """
:root {
    /* Define logical accent colors that remain in the orange/neutral family */
    --color-seq: var(--button-primary-background-fill);
    --color-vid: #f7e8a6;
    --color-kf: #d9a27c;
    --color-stop: #ff4b4b;
    --color-proj: #000000;
    --color-test: #9fb8d9;

}

/* 1. Status Bar & Link Logic (Theme Orange) */
#status_indicator a, a {
    color: var(--button-primary-background-fill) !important;
    text-decoration: none !important;
}

/* 2. Professional Hierarchy Icons */
/* Sequence: Structural/Primary (Orange) */
.seq-icon { color: var(--color-seq) !important; font-weight: bold; }

/* Keyframe: Content/Anchor (Purple) */
.kf-icon { color: var(--color-kf) !important; }

/* In-between: Transitions (Teal) */
.ib-icon { color: var(--color-vid) !important; }

/* 3. Panel Association Threads */
.seq-theme { border-left: 4px solid var(--color-seq) !important; }
.kf-theme { border-left: 4px solid var(--color-kf) !important; }
.vid-theme { border-left: 4px solid var(--color-vid) !important; }
.stop-theme { border-left: 4px solid var(--color-stop) !important; }
.proj-theme { border-left: 4px solid var(--color-proj) !important; }

/* 4. Radio Item Compactness */
#outline_list .wrap {
    padding: 2px 8px !important;
}

#editor-empty-callout {
    text-align: center !important;
    padding: 60px !important;
    border: 2px dashed var(--body-text-color-subdued) !important;
    border-radius: 12px !important;
    margin-top: 20px !important;
    opacity: 0.5;
}

/* --- Node Selector (Left Panel) --- */
#outline_list .wrap > label {
    display: flex !important;
    align-items: center !important;
    gap: 8px !important;
    padding: 6px 12px !important;
    border-radius: 6px !important;
    /* Force width logic to prevent horizontal jitter */
    width: 100% !important;
    max-width: 100% !important;
    box-sizing: border-box !important;
    color: var(--body-text-color) !important;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

/* Stabilize the outer container */
#outline-list-container { 
    max-height: calc(100vh - 450px); 
    overflow-y: auto !important; 
    overflow-x: hidden !important; /* Prevents horizontal shake */
    border: 1px solid #444; 
    border-radius: 8px; 
    padding: 8px; 
}

/* Color sequences Orange (Primary hierarchy) */
#outline-list-container label:has(input[value^="seq"]),
#outline-list-container label:has(input[value^="shot"]) { 
    color: var(--color-seq) !important; 
}

/* Keyframes Purple */
#outline-list-container label:has(input[value^="id"]) { 
    color: var(--color-kf) !important; 
}

/* In-betweens Teal */
#outline-list-container label:has(input[value^="vid"]) { 
    color: var(--color-vid) !important;
}



# #outline-list-container { 
#     max-height: calc(100vh - 250px + 0.5rem); 
#     overflow-y: auto; 
#     overflow-x: hidden; 
#     border: 1px solid #444; 
#     border-radius: 8px; 
#     padding: 8px; 
# }

#outline-list-container { 
    /* Increasing 250px to 450px makes the box shorter */
    max-height: calc(100vh - 450px); 
    overflow-y: auto; 
    overflow-x: hidden; 
    border: 1px solid #444; 
    border-radius: 8px; 
    padding: 8px; 
}

.pose-buttons-col {
    justify-content: center;
}

@media (max-width: 1280px) { 
    #outline-list-container { max-height: calc(220px + 0.5rem); } 
}

# /* --- Accordion Header Styling (Inspector & Curation) --- */
# .themed-accordion > .label-wrap {
#     /* Removed border-left highlight bar */
#     padding-left: 12px !important;
#     border-radius: 4px;
# }


/* Disable full-length side-thread borders */
.seq-theme, .kf-theme, .vid-theme, .stop-theme, .proj-theme { 
    border-left: none !important; 
}

/* Remove border from the header wrapper too */
.themed-accordion > .label-wrap {
    border-left: none !important;
    padding-left: 12px !important;
    position: relative; /* Required for the pip positioning */
}


/* --- Curate Tab Scrolling Container --- */
#curate-items-container {
    max-height: 70vh; /* Limit height */
    overflow-y: auto; /* Enable vertical scrollbar when needed */
    padding-right: 10px; /* Add some padding so scrollbar doesn't overlap content */
    border: 1px solid #444; /* Optional: Add border for visual separation */
    border-radius: 8px; /* Optional: Rounded corners */
    padding: 8px; /* Optional: Inner padding */
}
/* --- Curate Tab Navigation Buttons --- */
.curate-nav-button {
    min-height: 100px !important; /* Adjust height as needed */
    height: 100%;
}
.gradio-container {
    padding-top: 4px !important;
    overflow-x: hidden !important;
}
/* Generation Defaults — model family accent block */
.key-choice {
    border: none !important;
    border-left: 3px solid #fb7117 !important;
    border-radius: 0 !important;
    padding: 10px 12px 10px 10px !important;
    margin: 0 !important;
    background: linear-gradient(90deg, rgba(251, 113, 23, 0.12), rgba(251, 113, 23, 0) 70%) !important;
    box-shadow: none !important;
    max-width: 100% !important;
    box-sizing: border-box !important;
    --color-accent: #fb7117;
    --checkbox-label-background-fill-selected: rgba(251, 113, 23, 0.14);
    --checkbox-label-border-color-selected: #fb7117;
    --checkbox-label-text-color-selected: #fff;
}
.key-choice > .block,
.key-choice .form,
.key-choice .block,
.key-choice .styler,
.key-choice div[class*="block"] {
    border: none !important;
    border-left: none !important;
    border-radius: 0 !important;
    box-shadow: none !important;
    background: transparent !important;
    padding: 0 !important;
    margin: 0 !important;
}
.key-choice input[type="radio"]:checked + span,
.key-choice label.selected,
.key-choice .selected,
.key-choice .wrap input:checked + label {
    background: rgba(251, 113, 23, 0.14) !important;
    border-color: #fb7117 !important;
    color: #fff !important;
}
.key-choice .wrap-inner > div,
.key-choice .wrap > div {
    border-color: #fb7117 !important;
}
.lora-nested-ceiling {
    border-left: 2px solid #2c3a4c !important;
    padding-left: 14px !important;
    margin: -4px 0 10px 27px !important;
    gap: 0 !important;
    background: transparent !important;
}
.lora-nested-ceiling > .column,
.lora-nested-ceiling .block {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    padding: 0 !important;
}
.lora-nested-ceiling .label-wrap {
    padding-top: 0 !important;
    min-height: 0 !important;
}

#inbetween_len,
#inbetween_len > div {
    max-width: 100% !important;
    box-sizing: border-box !important;
}
#inbetween_len .wrap,
#inbetween_len > div:nth-child(2) {
    flex-wrap: wrap !important;
    max-width: 100% !important;
    overflow-x: hidden !important;
}
#inbetween_len label {
    min-width: 0 !important;
    flex: 0 1 auto !important;
}
#generation_defaults {
    overflow-x: hidden !important;
}
#generation_defaults > .wrap,
#generation_defaults .column {
    max-width: 100% !important;
    min-width: 0 !important;
    overflow-x: hidden !important;
}

/* Consolidated Generation Panel Styles */
.generation-card {
    padding: 12px !important;
    border: 1px solid var(--border-color-primary) !important;
    border-radius: 8px !important;
    background: var(--background-fill-secondary) !important;
}
#save-status-container {
    max-width: 250px; /* Adjust this width as needed */
    min-width: 100px; /* Prevent it from becoming too small */
    overflow: hidden;
    white-space: nowrap;
    display: block; /* Ensures markdown is treated as a block */
}
#save-status-container p { /* Target the inner <p> tag */
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    margin: 0; /* Remove default markdown margins */
    padding: 0;
}

/* Create the "Small Highlight" Pip */
.themed-accordion > .label-wrap::before {
    content: "";
    position: absolute;
    left: 0;
    top: 50%;
    transform: translateY(-50%);
    width: 4px;
    height: 12px; /* Small height matching your green circles */
    border-radius: 2px;
    background-color: var(--body-text-color-subdued); /* Default neutral */
}

/* Color track the Pips based on theme class */
.seq-theme > .label-wrap::before { 
    background-color: var(--color-seq) !important; 
}
.kf-theme > .label-wrap::before { 
    background-color: var(--color-kf) !important; 
}
.vid-theme > .label-wrap::before { 
    background-color: var(--color-vid) !important; 
}
.stop-theme > .label-wrap::before { 
    background-color: var(--color-stop) !important; 
}
.proj-theme > .label-wrap::before { 
    background-color: var(--color-proj) !important; 
}

/* --- Compact Header Styling --- */
#header-row {
    align-items: center !important;
    padding-top: 0px !important;
}

/* Separate the tab nav from the header cluster above it */
#header-tab-separator {
    border: none !important;
    border-top: 1px solid #2a2a30 !important;
    margin: 10px 0 0 0 !important;
    height: 0 !important;
    padding: 0 !important;
}

#app-title h3 {
    margin: 0 !important;
    white-space: nowrap;
}

#app-title small {
    font-size: 0.6em;
    opacity: 0.7;
    margin-left: 8px;
}

#project-path-display {
    resize: none !important;
    font-family: monospace;
    font-size: 0.85rem;
    opacity: 0.7;
}
/* --- Final Header Desktop Alignment --- */
#header-utility-col {
    min-width: 330px !important;
    flex-grow: 0 !important;
}

/* --- Final Header Desktop Alignment --- */
#header-utility-col {
    min-width: 330px !important;
    flex-grow: 0 !important;
}

.header-utility-row {
    display: flex !important;
    flex-direction: row !important;
    align-items: center !important;
    justify-content: space-between !important; /* Pushes text left, button right */
    gap: 10px !important;
}

#status_indicator {
    resize: none !important;
    text-align: left !important;
    font-size: 0.85rem;
    white-space: nowrap;
}

/* File / ComfyUI status / save-status — one compact line, wraps on narrow viewports */
#header-status-line {
    display: flex !important;
    flex-wrap: wrap !important;
    align-items: baseline;
    gap: 12px;
    row-gap: 2px;
    margin-top: -8px;
}
#header-status-line > * { flex: 0 0 auto !important; width: auto !important; margin: 0 !important; }

#header-refresh-btn {
    margin-top: 0px !important;
    width: 85px !important;
    flex-grow: 0 !important; /* Prevents button from stretching */
}


/* Header utility row */
.compact {
    gap: 8px !important;
}


.constrained-video video {
    max-width: 100%;
    max-height: 60vh;
    width: auto;
    height: auto;
    object-fit: contain;
}

/* Header toolbar — Save+Autosave and Reload+Autoload, paired into two
   independent bordered cards side by side. */
#header-toolbar {
    display:flex !important; flex-direction:row !important; flex-wrap:wrap !important;
    align-items:flex-start !important; gap:6px; flex:0 0 auto;
    width:fit-content !important; min-width:0 !important;
    justify-content:flex-end;
}
#header-save-card, #header-reload-card {
    display:flex !important; flex-direction:row !important; flex-wrap:nowrap !important;
    align-items:center !important; gap:5px; flex:0 0 auto;
    width:auto !important; min-width:0 !important;
    background:transparent !important; border:none !important; padding:4px 6px;
}
#header-reload-card {
    border-left: 1px solid #34343c !important;
    padding-left: 12px !important;
    margin-left: 4px !important;
}
#header-save-card > *, #header-reload-card > * { flex:0 0 auto !important; width:auto !important; min-width:0 !important; }
#header-save-card .form, #header-reload-card .form {
    width:auto !important; min-width:0 !important; padding:0 !important;
    border:none !important; background:transparent !important;
}
#header-save-card .gr-button, #header-save-card button,
#header-reload-card .gr-button, #header-reload-card button {
    width:auto !important; min-width:0 !important; white-space:nowrap !important;
}
#header-save-card label, #header-reload-card label { white-space:nowrap !important; }

#header-save-btn button {
    background:#d9602b !important; color:#fff !important; border:none !important;
    font-weight:600 !important; padding:5px 10px !important;
}
#header-save-btn button:hover { background:#e9722f !important; }

#header-reload-btn button {
    background:#1c1c21 !important; color:#e8e8ea !important;
    border:1px solid #34343c !important; font-weight:600 !important; padding:5px 10px !important;
}
#header-reload-btn button:hover { background:#26262c !important; }

#header-autosave-toggle label, #header-autoload-toggle label { color:#c4c4ca !important; font-size:11px !important; }
#header-autosave-toggle, #header-autoload-toggle { min-width:0 !important; }

#header-status-text {
    resize: none !important;
    text-align: left !important;
    font-size: 0.85rem;
    white-space: nowrap;
}

"""

with gr.Blocks(title=APP_TITLE, theme=theme, css=custom_css) as demo:
    gr.HTML("""
    <style>
      .gradio-textbox textarea {
        max-height: 250px;
        overflow-y: auto !important;
        resize: vertical;
      }
    </style>
    """)
    gr.HTML("""
    <script>
      function autosizeAllTextareas() {
        const textareas = document.querySelectorAll('.gradio-textbox textarea');
        textareas.forEach(el => {
          el.style.height = 'auto';
          el.style.height = el.scrollHeight + 'px';
        });
      }
      function debounce(func, timeout = 100){
        let timer;
        return (...args) => {
          clearTimeout(timer);
          timer = setTimeout(() => { func.apply(this, args); }, timeout);
        };
      }
      const processChange = debounce(() => autosizeAllTextareas());
      const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
          if (mutation.type === 'childList' || mutation.type === 'characterData') {
            processChange();
          }
        });
      });
      function observeGradioApp() {
        const gradioApp = document.querySelector('gradio-app');
        if (gradioApp) {
          autosizeAllTextareas();
          observer.observe(gradioApp, {
            childList: true,
            subtree: true,
            characterData: true
          });
        } else {
          setTimeout(observeGradioApp, 100);
        }
      }
      document.addEventListener('DOMContentLoaded', observeGradioApp);
    </script>
    """)

    # NEW: Form Registry
    form = ProjectFormRegistry()

    with gr.Row(elem_id="header-row"):
        # Column 1: Title block
        with gr.Column(scale=1, min_width=300):
            gr.Markdown(f"### {APP_TITLE} <small>v 1.1</small>", elem_id="app-title")
            with gr.Row(elem_id="header-status-line"):
                project_name_header = gr.Markdown("", elem_id="project-path-display")
                comfyui_status_md = gr.Markdown(elem_id="status_indicator")
                header_status_text = gr.Markdown("", elem_id="header-status-text")

        # Column 2: Utility cluster
        with gr.Column(elem_id="header-utility-col"):
            with gr.Row(elem_classes=["header-utility-row"]):
                refresh_all_btn = gr.Button("Refresh Data", variant="secondary", size="sm", elem_id="header-refresh-btn", visible=False)
                with gr.Row(elem_id="header-toolbar"):
                    with gr.Row(elem_id="header-save-card"):
                        save_now_btn = gr.Button("Save", variant="primary", size="sm", elem_id="header-save-btn")
                        autosave_enabled = gr.Checkbox(label="Auto", value=True, elem_id="header-autosave-toggle")
                    with gr.Row(elem_id="header-reload-card"):
                        reload_btn = gr.Button("Reload", variant="secondary", size="sm", elem_id="header-reload-btn")
                        autoload_enabled = gr.Checkbox(label="Auto", value=False, elem_id="header-autoload-toggle")

    autoload_timer = gr.Timer(value=10, active=False)
    _autoload_last_mtime = gr.State(value=0.0)
    mode_watch_timer = gr.Timer(value=10, active=True)
    _last_seen_active_writer = gr.State(value="")
    _last_known_fingerprint = gr.State(value="")


    # Shared state
    settings_json     = gr.State(value=settings_json_init)
    current_file_path = gr.State(value="")
    generation_result_buffer = gr.State(value={})
    _file_path_snapshot = [""]
    # PHASE 3: Removed json_load_buffer and temp_file_path_buffer - no longer needed
    lora_file_state   = gr.State(value=[])
    comfyui_api_base = gr.State(value=settings.get("comfy", {}).get("api_base", "http://127.0.0.1:8188"))

    gr.HTML('<hr id="header-tab-separator">')

    with gr.Tabs() as main_tabs:

        # ---------------------- Project Tab ----------------------
        with gr.TabItem("Project", id="project_tab"):
            
            # ============================================================
            # SECTION 1: Project File Management
            # ============================================================
            with gr.Accordion("Project File", open=True, elem_classes=["themed-accordion", "proj-theme"]):
                with gr.Group(visible=True) as file_picker_group:
                    with gr.Row():
                        with gr.Column(scale=3):
                            file_picker = gr.Dropdown(label="Current Project File",  choices=[], interactive=False, allow_custom_value=False, filterable=False)
                        with gr.Column(scale=1):
                            new_btn = gr.Button("New Project", variant="primary")
                            

                with gr.Group(visible=False) as new_file_group:
                    with gr.Row():
                        with gr.Column(scale=3):
                            new_file_name = gr.Textbox(label="New Project Name", placeholder="Enter name (without .json)", value=_ts_name())
                        with gr.Column(scale=1):
                            create_new_btn = gr.Button("Create", variant="primary")
                            cancel_new_btn = gr.Button("Cancel")

            # ============================================================
            # SECTION 2: Generation Defaults
            # ============================================================
            with gr.Accordion("Generation Defaults", open=True, visible=False, elem_id="generation_defaults", elem_classes=["themed-accordion", "proj-theme"]) as project_basics_accordion:
                with gr.Row(equal_height=False):
                    with gr.Column(scale=1, min_width=0):
                        gr.Markdown("### Image")
                        with gr.Group(elem_classes=["key-choice"]):
                            image_model_family_radio = form.add(
                                "project.image_model_family",
                                gr.Radio(
                                    ["Default", "Custom"],
                                    label="Image Model Family",
                                    value="Default",
                                    info="Default: pose-driven workflows. Custom: fixed project workflow.",
                                ),
                                default=IMAGE_MODEL_FAMILY_DEFAULT,
                                to_ui=image_family_json_to_label,
                                to_json=image_family_label_to_json,
                            )
                            default_workflow_dd = form.add(
                                "project.default_workflow_json",
                                gr.Dropdown(
                                    label="Image Workflow",
                                    info="Custom mode only. Baseline for new keyframes and asset generation.",
                                    choices=(
                                        cb_list_workflow_files(str(WORKFLOWS_DIR), DEFAULT_PROJECT_WORKFLOW_FILENAME).get(
                                            "choices", [""]
                                        )[1:]
                                    ),
                                    interactive=True,
                                    filterable=False,
                                    allow_custom_value=False,
                                ),
                                default=DEFAULT_PROJECT_WORKFLOW_FILENAME,
                            )
                        gr.Markdown("**LORA NORMALIZATION**")
                        norm_fg_en = form.add(
                            "project.lora_normalization.fg_enabled",
                            gr.Checkbox(label="Foreground (characters)", value=True),
                            default=True,
                            to_json=bool,
                        )
                        with gr.Row(elem_classes=["lora-nested-ceiling"]):
                            norm_fg_max = form.add(
                                "project.lora_normalization.fg_max",
                                gr.Number(
                                    label="Weight ceiling",
                                    value=1.5,
                                    step=0.1,
                                    minimum=0,
                                    scale=0,
                                ),
                                default=1.5,
                                to_json=float,
                            )
                        norm_bg_en = form.add(
                            "project.lora_normalization.bg_enabled",
                            gr.Checkbox(label="Background (styles & locations)", value=True),
                            default=True,
                            to_json=bool,
                        )
                        with gr.Row(elem_classes=["lora-nested-ceiling"]):
                            norm_bg_max = form.add(
                                "project.lora_normalization.bg_max",
                                gr.Number(
                                    label="Weight ceiling",
                                    value=1.5,
                                    step=0.1,
                                    minimum=0,
                                    scale=0,
                                ),
                                default=1.5,
                                to_json=float,
                            )
                    with gr.Column(scale=1, min_width=0):
                        gr.Markdown("### Video")
                        with gr.Group(elem_classes=["key-choice"]):
                            video_model_family_radio = form.add(
                                "project.video_model_family",
                                gr.Radio(
                                    ["Default", "Custom"],
                                    label="Video Model Family",
                                    value="Default",
                                    info="Default: Wan i2v_base workflow. Custom: pick your video workflow.",
                                ),
                                default=VIDEO_MODEL_FAMILY_DEFAULT,
                                to_ui=video_family_json_to_label,
                                to_json=video_family_label_to_json,
                            )
                            video_workflow_dd = form.add(
                                "project.inbetween_generation.video_workflow_json",
                                gr.Dropdown(
                                    label="Video Workflow",
                                    info="Workflow used for in-between / i2v generation.",
                                    choices=(
                                        cb_list_workflow_files(
                                            str(WORKFLOWS_DIR), DEFAULT_VIDEO_WORKFLOW_FILENAME
                                        ).get("choices", [""])[1:]
                                    ),
                                    interactive=True,
                                    filterable=False,
                                    allow_custom_value=False,
                                ),
                                default=DEFAULT_VIDEO_WORKFLOW_FILENAME,
                                to_ui=video_workflow_path_to_dropdown,
                                to_json=video_workflow_dropdown_to_path,
                            )
                        video_fps = form.add(
                            "project.inbetween_generation.fps",
                            gr.Number(
                                label="Frame rate",
                                info="Injected when workflow has THM-FrameRate / THM-FPS.",
                                precision=0,
                                minimum=1,
                            ),
                            default=16,
                            to_json=float,
                        )
                        vid_dur = form.add(
                            "project.inbetween_generation.duration_default_sec",
                            gr.Radio(
                                DUR_CHOICES,
                                label="Default In-Between Length (seconds)",
                                elem_id="inbetween_len",
                            ),
                            default="5",
                            to_ui=_dur_to_choice,
                            to_json=int,
                        )
                        video_steps = form.add(
                            "project.inbetween_generation.video_steps_default",
                            gr.Number(
                                label="Video steps",
                                info=(
                                    "Total denoise steps for THM-KSampler chain, THM-Steps scheduler "
                                    "(e.g. LTXVScheduler), or legacy SlowMoPrimer / IterKSampler / "
                                    "WanFixedSeed triple only."
                                ),
                                precision=0,
                                minimum=1,
                            ),
                            default=14,
                            to_json=int,
                        )
                        gr.Markdown("**LORA NORMALIZATION**")
                        norm_vid_en = form.add(
                            "project.inbetween_generation.lora_normalization_enabled",
                            gr.Checkbox(label="Normalize video LoRAs", value=False),
                            default=False,
                            to_json=bool,
                        )
                        with gr.Row(elem_classes=["lora-nested-ceiling"]):
                            norm_vid_max = form.add(
                                "project.inbetween_generation.lora_normalization_max",
                                gr.Number(
                                    label="Weight ceiling",
                                    value=1.5,
                                    step=0.1,
                                    minimum=0,
                                    scale=0,
                                    interactive=False,
                                ),
                                default=1.5,
                                to_json=float,
                            )

            # ============================================================
            # SECTION 4: Style & Model Settings
            # ============================================================
            with gr.Accordion("Look Development", open=True, visible=False,elem_classes=["themed-accordion", "proj-theme"]) as project_style_accordion:
                # Dimensions
                
                # Main style configuration
                with gr.Row():
                    # Left: Style Prompt
                    with gr.Column(scale=1):
                        with gr.Group():
                            # gr.Markdown("**Global Prompts**")
                            style_tags = form.add("project.style_prompt", 
                                gr.Textbox(label="Global Look Prompt", info="Applies to all generations in this project", lines=8), 
                                default="")
                            with gr.Row():
                                neg_global = form.add("project.negatives.global", gr.Textbox(label="Global Negative", info="Applies to all generations in this project", lines=1), default="")
                                neg_kf = form.add("project.negatives.keyframes_all", gr.Textbox(label="Keyframe Negative", info="Applies to all Keyframes in this project", lines=1), default="")
                            with gr.Row():
                                neg_i2v = form.add("project.negatives.inbetween_all", gr.Textbox(label="In-between Negative", info="Applies to all In-Betweens in this project", lines=1), default="")
                                neg_heal = form.add("project.negatives.heal_all", gr.Textbox(label="Heal Pass Negative", info="Applies to all 2CHAR Keyframes in this project", lines=1), default="")


                    # Right: Model & Generation Parameters
                    with gr.Column(scale=1):
                        with gr.Group():
                            gr.Markdown("**Dimensions**")
                            with gr.Row():
                                width = form.add("project.width", gr.Number(label="Width", precision=0, minimum=1), default=1152, to_json=int)
                                height = form.add("project.height", gr.Number(label="Height", precision=0, minimum=1), default=768, to_json=int)

                            vid_quarter = form.add("project.inbetween_generation.quarter_size_video", 
                                gr.Checkbox(label="Quarter Size Video", info="Recommended in conjunction with 2x upscaling"), 
                                default=True)

                    
                        with gr.Group(visible=True) as kf_model_config_group:
                            gr.Markdown("**Keyframe Model Configuration**")
                            with gr.Row():
                                model_dd = form.add("project.model", 
                                    gr.Dropdown(label="Model", info="Applies to all Keyframes in this project", choices=[], interactive=True, allow_custom_value=False, filterable=False), 
                                    default="")
                            with gr.Row():
                                kf_steps = form.add("project.keyframe_generation.steps", 
                                    gr.Number(label="Steps", info="Number of denoising iterations (higher = more refined)", 
                                    precision=0, minimum=1), default=30, to_json=int)
                                kf_cfg = form.add("project.keyframe_generation.cfg", 
                                    gr.Number(label="CFG Scale", info="Guidance strength (higher = more prompt adherence)", 
                                    step=0.5, minimum=1.0), default=4.0)
                            
                            with gr.Row():
                                kf_sampler_name = form.add("project.keyframe_generation.sampler_name", 
                                    gr.Dropdown(label="Sampler", choices=SAMPLER_CHOICES, interactive=True, allow_custom_value=False, filterable=False), 
                                    default="dpmpp_2m_sde")
                                kf_scheduler = form.add("project.keyframe_generation.scheduler", 
                                    gr.Dropdown(label="Scheduler", choices=SCHEDULER_CHOICES, interactive=True, allow_custom_value=False, filterable=False), 
                                    default="karras")


                with gr.Row():



                    # ========== RIGHT COLUMN: Create New Look ==========
                    with gr.Column(scale=1):
                        # gr.Markdown("### Create New Look")
                        
                        with gr.Row():
                            # Generate button
                            test_style_btn = gr.Button("Generate Preview", variant="primary")
                            style_save_btn = gr.Button("Save to Project Look Library", scale=1, variant="secondary")
                        # Preview display
                        style_test_image = gr.Image(
                            label="Look Preview", 
                            interactive=False, 
                            height=300, 
                            type="filepath"
                        )
                        style_save_status = gr.Markdown("", visible=True)

                        # Test scene selection

                        style_test_context = gr.Dropdown(
                            label="Pick a Scene", 
                            # info="Choose a preset scenario for testing this style",
                            choices=list_style_test_options(project_json_init),
                            interactive=True,
                            allow_custom_value=False
                        )

                        
                
                        # ========== BOTTOM: Advanced Log (Full Width) ==========
                        with gr.Accordion("Preview Log", open=False):
                            style_test_log = gr.Textbox(
                                lines=8, 
                                interactive=False, 
                                autoscroll=True,
                                show_label=False
                            )

                    # ========== LEFT COLUMN: Existing Looks ==========
                    with gr.Column(scale=1):
                        gr.Markdown("#### Project Look Library")
                        
                        # Gallery always visible (primary workflow)
                        style_gallery = gr.Gallery(
                            label="Saved Looks", 
                            show_label=False, 
                            columns=3, 
                            rows=3, 
                            height=400,
                            object_fit="contain", 
                            interactive=True, allow_preview=False
                        )
                        gallery_paths_state = gr.State([])
                        selected_image_path_state = gr.State("")
                        
                        # Recall controls below gallery
                        with gr.Row():
                            btn_refresh_gallery = gr.Button("Refresh Gallery", scale=1)
                            upload_look_btn = gr.UploadButton("Upload Look", scale=1, file_types=[".png"])
                            recall_style_btn = gr.Button("Use this Look", variant="primary", scale=1)
                        
                        status_recall = gr.Markdown("_Select an image above, then click Recall Settings_", elem_classes=["info-text"])



            # Visual containers removed, but components kept for JSON path registration
            with gr.Column(visible=False):
                img_iter = form.add("project.keyframe_generation.image_iterations_default", gr.Number(), default=1, to_json=int)
                kf_seed_start = form.add("project.keyframe_generation.sampler_seed_start", gr.Number(), default=0, to_json=int)
                kf_advance = form.add("project.keyframe_generation.advance_seed_by", gr.Number(), default=1, to_json=int)
                vid_iter = form.add("project.inbetween_generation.video_iterations_default", gr.Number(), default=1, to_json=int)
                vid_seed_start = form.add("project.inbetween_generation.seed_start", gr.Number(), default=0, to_json=int)
                vid_advance = form.add("project.inbetween_generation.advance_seed_by", gr.Number(), default=1, to_json=int)
                name = form.add("project.name", gr.Textbox(), default="")
                vid_prompt_template = form.add("project.inbetween_generation.prompt_template", gr.Textbox(), default="")
                vid_seed_target = form.add("project.inbetween_generation.seed_target_title", gr.Textbox(), default="SlowMoPrimer")
                vid_seed_exclude = form.add("project.inbetween_generation.seed_exclude_title", gr.Textbox(), default="WanFixedSeed")    



            with gr.Accordion("JSON", open=False, visible=False):
                gr.Markdown("**Current Project (JSON preview)**")
                json_renderer = gr.Code(language="json", 
                    value=json.dumps(project_json_init, indent=2, ensure_ascii=False), 
                    visible=True, elem_id="json_renderer")
                preview_code = gr.State(value=project_json_init)
                _preview_snapshot = [project_json_init]

                def _track_preview_snapshot(data):
                    if data is not None:
                        _preview_snapshot[0] = data

                preview_code.change(
                    fn=lambda x: json.dumps(x, indent=2, ensure_ascii=False),
                    inputs=[preview_code],
                    outputs=[json_renderer],
                    queue=False,
                    show_progress="hidden",
                )
                preview_code.change(
                    fn=_track_preview_snapshot,
                    inputs=[preview_code],
                    outputs=[],
                    queue=False,
                    show_progress="hidden",
                )

            file_op_outputs = [preview_code, current_file_path]

            def _save_now_with_status(file_path, project_data, settings_str):
                cb_save_project(file_path, project_data, settings_str)
                return "saved ✓", _file_fingerprint(file_path)

            save_now_btn.click(
                fn=_save_now_with_status,
                inputs=[current_file_path, preview_code, settings_json],
                outputs=[header_status_text, _last_known_fingerprint],
                queue=False,
                show_progress="hidden",
            )

        # ---------------------- Assets Tab ----------------------
        with gr.TabItem("Assets", interactive=False) as assets_tab:
            # pose_gallery, poses_dir_state, char_inject_dd, setting_inject_dd, style_inject_dd = build_assets_tab(preview_code, settings_json, features)
            (
                pose_gallery,
                poses_dir_state,
                char_inject_dd,
                char_gen_inject_dd,
                setting_inject_dd,
                setting_gen_inject_dd,
                style_inject_dd,
                style_gen_inject_dd,
                char_reference_save_btn,
                setting_reference_save_btn,
                style_reference_save_btn,
                char_gen_prompt_group,
                char_model_settings_group,
                char_reflib_group,
                setting_gen_prompt_group,
                setting_model_settings_group,
                setting_reflib_group,
                style_gen_prompt_group,
                style_model_settings_group,
                style_reflib_group,
            ) = build_assets_tab(preview_code, settings_json, current_file_path, features)
        # ---------------------- Editor Tab ----------------------
        with gr.TabItem("Editor", id="editor_tab", interactive=False) as editor_tab:
            # kf_workflow_json, kf_pose, vid_lora, node_selector, node_selector_outputs, seq_lora, kf_pose_gallery, kf_lora = build_editor_tab(
            kf_workflow_json, kf_pose, vid_lora, node_selector, selected_node, node_selector_outputs, seq_lora, kf_pose_gallery, kf_lora, proj_len = build_editor_tab(
        # with gr.TabItem("Editor", id="editor_tab", interactive=False) as editor_tab:
        #     kf_workflow_json, kf_pose, vid_lora, node_selector, node_selector_outputs, seq_lora, kf_pose_gallery = build_editor_tab(
                preview_code,
                settings_json,
                current_file_path,
                generation_result_buffer,
                features,
                comfyui_status_md=comfyui_status_md,
                comfyui_api_base=comfyui_api_base,
            )
        # ---------------------- Curation Tab ----------------------
        with gr.Tab("Curation", id="curate_tab", visible=False, interactive=False) as curate_tab:
            curate_mode_radio, curate_page_md, curate_rows = build_curate_tab(preview_code)

        # ---------------------- Run Tab ----------------------
        with gr.TabItem("Utilities", interactive=False) as utilities_tab:

            (
                run_images_btn, run_videos_btn,
                img_iter_run,
                vid_iter_run,
                status_window,
                duplicate_proj_btn,
                copy_group, copy_path, confirm_copy_btn, cancel_copy_btn
            ) = build_run_tab(current_file_path, preview_code, settings_json, form=form, features=features)

            img_iter = img_iter_run
            vid_iter = vid_iter_run




        workspace_dir = gr.State(value=settings.get("workspace_root", os.getcwd()))
        models_dir = gr.State(value=settings.get("models_root", os.getcwd()))
        loras_dir = gr.State(value=settings.get("loras_root", ""))


    locked_ui_components = [
        project_basics_accordion,
        project_style_accordion,
        assets_tab,
        editor_tab,
        curate_tab,
        utilities_tab,
        json_renderer
    ]

    refresh_sink = gr.State()

    def _master_refresh(
        workspace_dir,
        models_dir,
        loras_dir,
        project_json,
        current_model,
        current_lora,
        current_kf_workflow,
        current_video_workflow,
        current_pose,
        current_project,
    ):
        kf_workflow = current_kf_workflow
        video_workflow = current_video_workflow
        if isinstance(project_json, dict):
            if not str(kf_workflow or "").strip():
                kf_workflow = effective_default_workflow_filename(project_json)
            if not str(video_workflow or "").strip():
                video_workflow = resolve_project_video_workflow(project_json)
        if video_workflow and not str(video_workflow).endswith(".json"):
            video_workflow = video_workflow_dropdown_to_path(video_workflow)
        return cb_master_refresh(
            workspace_dir,
            models_dir,
            loras_dir,
            project_json,
            current_model,
            current_lora,
            kf_workflow,
            video_workflow,
            current_pose,
            current_project,
        )

    master_refresh_inputs = [
        workspace_dir,
        models_dir,
        loras_dir,
        preview_code,
        model_dd,
        gr.State(None),
        kf_workflow_json,
        video_workflow_dd,
        kf_pose,
        file_picker,
    ]
    master_refresh_outputs = [
        file_picker,
        model_dd,
        lora_file_state,
        kf_workflow_json,
        video_workflow_dd,
        refresh_sink,
        kf_pose_gallery,
    ]





    curate_tab.select(
        fn=_trigger_autosave,
        inputs=[current_file_path, preview_code, settings_json, autosave_enabled, _last_known_fingerprint],
        outputs=[autosave_enabled, _last_known_fingerprint, header_status_text]
    ).then(
        fn=curate_refresh,
        inputs=[preview_code, curate_mode_radio],
        outputs=[gr.State(), curate_page_md] + curate_rows
    )


    def _refresh_pose_gallery_on_assets_tab(pj):
        poses_dir = get_project_poses_dir(pj)
        if poses_dir:
            return get_pose_gallery_list(str(poses_dir))
        return []

    assets_lora_dropdowns = [
        char_inject_dd,
        char_gen_inject_dd,
        setting_inject_dd,
        setting_gen_inject_dd,
        style_inject_dd,
        style_gen_inject_dd,
    ]
    editor_lora_dropdowns = [vid_lora, seq_lora, kf_lora]

    def _apply_lora_choices_from_state(lora_list):
        return broadcast_lora_choices(lora_list, assets_lora_dropdowns)

    def _apply_editor_lora_choices_from_state(lora_list):
        return broadcast_lora_choices(lora_list, editor_lora_dropdowns)

    assets_tab.select(
        fn=_trigger_autosave,
        inputs=[current_file_path, preview_code, settings_json, autosave_enabled, _last_known_fingerprint],
        outputs=[autosave_enabled, _last_known_fingerprint, header_status_text]
    ).then(
        fn=_apply_lora_choices_from_state,
        inputs=[lora_file_state],
        outputs=assets_lora_dropdowns,
        queue=False,
        show_progress="hidden",
    ).then(
        fn=_refresh_pose_gallery_on_assets_tab,
        inputs=[preview_code],
        outputs=[pose_gallery]
    )

    editor_tab.select(
        fn=_trigger_autosave,
        inputs=[current_file_path, preview_code, settings_json, autosave_enabled, _last_known_fingerprint],
        outputs=[autosave_enabled, _last_known_fingerprint, header_status_text]
    ).then(
        fn=_apply_editor_lora_choices_from_state,
        inputs=[lora_file_state],
        outputs=editor_lora_dropdowns,
        queue=False,
        show_progress="hidden",
    ).then(
        fn=_eh_node_selected,
        inputs=[preview_code, node_selector, gr.State(value=None)],  # cur_sel=None allows initial load
        outputs=node_selector_outputs,
        show_progress="hidden", queue=False
    )

    # Update project length when clip duration changes
    vid_dur.change(
        fn=lambda pj: gr.update(value=_project_len_text(pj)),
        inputs=[preview_code],
        outputs=[proj_len],
        show_progress="hidden",
        queue=False
    )



    all_lora_dropdowns = assets_lora_dropdowns + editor_lora_dropdowns

    def _get_lora_list_only(path):
        u = cb_list_model_files(path)
        return u.get("choices", []) if isinstance(u, dict) else []

    def _apply_all_lora_choices(lora_list):
        return broadcast_lora_choices(lora_list, all_lora_dropdowns)

    # Scan loras_root once at startup, then fill inject dropdown choice lists.
    demo.load(
        fn=_get_lora_list_only,
        inputs=[loras_dir],
        outputs=[lora_file_state],
        queue=False,
        show_progress="hidden"
    ).then(
        fn=_apply_all_lora_choices,
        inputs=[lora_file_state],
        outputs=all_lora_dropdowns,
        queue=False,
        show_progress="hidden",
    )



    # Consolidated Global Refresh Wiring
    refresh_all_btn.click(
        fn=_master_refresh,
        inputs=master_refresh_inputs,
        outputs=master_refresh_outputs
    ).then(
        fn=_apply_all_lora_choices,
        inputs=[lora_file_state],
        outputs=all_lora_dropdowns,
        queue=False,
        show_progress="hidden"
    )



    new_btn.click(
        lambda: (gr.update(visible=True),gr.update(visible=False)),
        outputs=[new_file_group,file_picker_group]
    ).then(
        lambda: _ts_name(), 
        outputs=[new_file_name]
    )
    cancel_new_btn.click(
        lambda: (gr.update(visible=False), gr.update(visible=True)),
        outputs=[new_file_group,file_picker_group]
    )

    # PHASE 3: Simplified load wrapper - calls atomic load_project_complete
    # Debouncing: Track last loaded file to prevent double loads
    _last_load_cache = {"filepath": None, "timestamp": 0}
    

    def load_and_update(filepath: str, settings_str: str, force: bool = False, preserve_selection: bool = False):
        """Wrapper for direct loads (file_picker.change, reload_btn) - no file_picker update

        Args:
            filepath: Path to project file (may be just filename from dropdown)
            settings_str: Settings JSON string
            force: If True, bypass debounce (for reload button)
            preserve_selection: If True, don't reset the outline selection to the first node
        """
        import time
        
        print(f"[LOAD_WRAPPER] Called with filepath={filepath}, force={force}")
        
        # CRITICAL: Guard against None filepath during initialization
        if filepath is None:
            print(f"[LOAD_WRAPPER] Skipping load - filepath is None (initialization)")
            return [gr.update()] * len(load_outputs_no_picker)  # Return no-op updates
        
        # Resolve bare filename to workspace path
        from pathlib import Path
        filepath_path = Path(filepath)
        if not filepath_path.is_absolute() and not filepath_path.parent.name:
            workspace = settings.get("workspace_root", "./projects")
            filepath = str(Path(workspace) / filepath)
            print(f"[LOAD_WRAPPER] Resolved filename to full path: {filepath}")
        
        # DEBOUNCING: Skip if we just loaded this file within last 1 second (unless forced)
        current_time = time.time()
        if not force and (_last_load_cache["filepath"] == filepath and 
                        current_time - _last_load_cache["timestamp"] < 1.0):
            print(f"[LOAD_WRAPPER] DEBOUNCED - Already loaded {filepath} {current_time - _last_load_cache['timestamp']:.2f}s ago")
            return [gr.update()] * len(load_outputs_no_picker)  # Return no-op updates
        
        try:
            result = load_project_complete(filepath, settings_str, form, get_style_test_images, preserve_selection=preserve_selection)
            print(f"[LOAD_WRAPPER] Got result with {len(result)} items")

            _file_path_snapshot[0] = filepath
            if result and isinstance(result[0], dict):
                _preview_snapshot[0] = result[0]

            # Update debounce cache
            _last_load_cache["filepath"] = filepath
            _last_load_cache["timestamp"] = current_time

            form_count = len(form.get_outputs())
            file_picker_index = 4 + form_count  # preview + path + outline + selected_node + form
            target_len = len(load_outputs_no_picker)

            temp_list = list(result)

            if len(temp_list) > file_picker_index:
                temp_list.pop(file_picker_index)

            temp_list = temp_list[:target_len]
            while len(temp_list) < target_len:
                temp_list.append(gr.update())

            print(f"[LOAD_WRAPPER] Returning {len(temp_list)} items (Synced to {target_len})")
            return tuple(temp_list)
        
        except Exception as e:
            print(f"[LOAD_WRAPPER] ERROR: {e}")
            import traceback
            traceback.print_exc()
            raise
    
    def load_and_update_with_picker(filepath: str, settings_str: str):
        """Wrapper for create/save-as - includes file_picker update (53 outputs)"""
        print(f"[LOAD_WRAPPER_PICKER] Called with filepath={filepath}")
        try:
            result = load_project_complete(filepath, settings_str, form, get_style_test_images)
            _file_path_snapshot[0] = filepath
            if result and isinstance(result[0], dict):
                _preview_snapshot[0] = result[0]
            temp_list = list(result)
            target_len = len(load_outputs_with_picker)
            temp_list = temp_list[:target_len]
            while len(temp_list) < target_len:
                temp_list.append(gr.update())
            return tuple(temp_list)
        except Exception as e:
            print(f"[LOAD_WRAPPER_PICKER] ERROR: {e}")
            import traceback
            traceback.print_exc()
            raise
    
    def load_and_update_force(filepath: str, settings_str: str):
        """Wrapper for reload button - bypasses debounce, preserves outline selection"""
        return load_and_update(filepath, settings_str, force=True, preserve_selection=True)

    def _apply_initial_mode_from_project(project_dict, file_path):
        """On a fresh file open (file_picker/create/save-as only, not Reload/Autoload),
        derive starting Autosave/Autoload from project.active_writer, and capture
        the file's current fingerprint as this session's baseline for detecting
        another human editor on a different device later."""
        global _autosave_last_time
        data = project_dict if isinstance(project_dict, dict) else {}
        active_writer = data.get("project", {}).get("active_writer")
        fingerprint = _file_fingerprint(file_path) if file_path else ""
        print(f"[MODE] _apply_initial_mode_from_project: active_writer={active_writer!r} file_path={file_path!r}")
        if active_writer == "agent":
            try:
                mtime = Path(file_path).stat().st_mtime if file_path else 0.0
            except OSError:
                mtime = 0.0
            # Bump the autosave debounce clock now, before the rest of the load
            # chain (e.g. first-node selection) can fire its own autosave trigger
            # with a not-yet-propagated, stale "autosave on" checkbox reading.
            _autosave_last_time = time.time()
            print(f"[MODE] -> starting in AGENT mode (autosave off, autoload on), mtime={mtime}")
            return gr.update(value=False), gr.update(value=True), gr.update(active=True), mtime, "agent", fingerprint
        print(f"[MODE] -> starting in UI mode (autosave on, autoload off)")
        return gr.update(value=True), gr.update(value=False), gr.update(active=False), 0.0, (active_writer or ""), fingerprint

    # Define all outputs for load operations in correct order
    # Debug: Let's check what form.get_outputs() actually returns
    form_outputs = form.get_outputs()
    print(f"[INIT] form.get_outputs() returned {len(form_outputs)} items, type={type(form_outputs)}")
    print(f"[INIT] master_refresh_outputs has {len(master_refresh_outputs)} items")
    print(f"[INIT] master_refresh_outputs = {[type(x).__name__ for x in master_refresh_outputs]}")
    print(f"[INIT] locked_ui_components has {len(locked_ui_components)} items")
    
    # CRITICAL FIX: Don't update file_picker when file_picker.change() triggers
    # Create outputs WITHOUT file_picker for direct load events
    # Define outputs without file_picker (for .change events)
    refresh_outputs_no_picker = [model_dd, lora_file_state, kf_workflow_json, video_workflow_dd, refresh_sink, kf_pose_gallery]

    load_outputs_no_picker = (
        [preview_code, current_file_path, node_selector, selected_node] +
        form_outputs +
        [model_dd, lora_file_state, kf_workflow_json, video_workflow_dd, refresh_sink, kf_pose_gallery] +
        [project_name_header, poses_dir_state, pose_gallery, style_gallery] +
        locked_ui_components
    )
    
    load_outputs_with_picker = (
        [preview_code, current_file_path, node_selector, selected_node] +
        form_outputs +
        master_refresh_outputs +
        [project_name_header, poses_dir_state, pose_gallery, style_gallery] +
        locked_ui_components
    )
    
    print(f"[INIT] load_outputs_no_picker has {len(load_outputs_no_picker)} components")
    print(f"[INIT] load_outputs_with_picker has {len(load_outputs_with_picker)} components")

    # PHASE 3: Create new project - single function that creates and loads
    def create_and_load(name: str, settings_str: str):
        """Create new project then load it atomically"""
        data, filepath = cb_create_new_project(name, settings_str)
        return load_project_complete(filepath, settings_str, form, get_style_test_images)
    
    create_new_btn.click(
        fn=create_and_load,
        inputs=[new_file_name, settings_json],
        outputs=load_outputs_with_picker,  # Include file_picker update to select new file
        queue=True,  # Sequential processing
        show_progress="minimal"
    ).then(
        fn=_apply_initial_mode_from_project,
        inputs=[preview_code, current_file_path],
        outputs=[autosave_enabled, autoload_enabled, autoload_timer, _autoload_last_mtime, _last_seen_active_writer, _last_known_fingerprint],
        queue=False,
        show_progress="hidden",
    ).then(
        fn=lambda: (gr.update(visible=False),gr.update(visible=True)),
        outputs=[new_file_group,file_picker_group],
        queue=False,
        show_progress="hidden"
    ).then(
        fn=sync_style_test_scene_dropdown,
        inputs=[preview_code],
        outputs=[style_test_context],
        queue=False,
        show_progress="hidden",
    )

    def _select_first_node_after_load(project_dict):
        """After load, select the first outline row to populate the editor."""
        data = project_dict if isinstance(project_dict, dict) else {}
        first_id = first_outline_node_id(data)
        if first_id:
            return _eh_node_selected(data, first_id, None)
        return tuple([gr.update()] * 67)

    def _reselect_node_after_reload(project_dict, previous_node_id):
        """After an in-place reload, stay on the same outline row instead of
        jumping to the first one. Falls back to the first row if the
        previously-selected node no longer exists (e.g. deleted elsewhere)."""
        data = project_dict if isinstance(project_dict, dict) else {}
        node_id = previous_node_id if outline_node_exists(data, previous_node_id) else first_outline_node_id(data)
        if node_id:
            return _eh_node_selected(data, node_id, None)
        return tuple([gr.update()] * 67)

    file_picker.change(
        fn=load_and_update,
        inputs=[file_picker, settings_json],
        outputs=load_outputs_no_picker,
        queue=True,
        show_progress="minimal"
    ).then(
        fn=_apply_initial_mode_from_project,
        inputs=[preview_code, current_file_path],
        outputs=[autosave_enabled, autoload_enabled, autoload_timer, _autoload_last_mtime, _last_seen_active_writer, _last_known_fingerprint],
        queue=False,
        show_progress="hidden",
    ).then(
        fn=_select_first_node_after_load,
        inputs=[preview_code],
        outputs=node_selector_outputs,
        show_progress="hidden", queue=False
    ).then(
        fn=_apply_editor_lora_choices_from_state,
        inputs=[lora_file_state],
        outputs=editor_lora_dropdowns,
        queue=False,
        show_progress="hidden",
    ).then(
        fn=sync_style_test_scene_dropdown,
        inputs=[preview_code],
        outputs=[style_test_context],
        queue=False,
        show_progress="hidden",
    )

    _pre_reload_node_id = gr.State(value="")

    reload_btn.click(
        fn=lambda nid: nid,
        inputs=[selected_node],
        outputs=[_pre_reload_node_id],
        queue=False,
        show_progress="hidden",
    ).then(
        fn=load_and_update_force,
        inputs=[current_file_path, settings_json],
        outputs=load_outputs_no_picker,
        queue=True,
        show_progress="minimal"
    ).then(
        fn=_reselect_node_after_reload,
        inputs=[preview_code, _pre_reload_node_id],
        outputs=node_selector_outputs,
        show_progress="hidden", queue=False
    ).then(
        fn=_apply_editor_lora_choices_from_state,
        inputs=[lora_file_state],
        outputs=editor_lora_dropdowns,
        queue=False,
        show_progress="hidden",
    ).then(
        fn=sync_style_test_scene_dropdown,
        inputs=[preview_code],
        outputs=[style_test_context],
        queue=False,
        show_progress="hidden",
    ).then(
        fn=_file_fingerprint,
        inputs=[current_file_path],
        outputs=[_last_known_fingerprint],
        queue=False,
        show_progress="hidden",
    ).then(
        fn=lambda: "reloaded",
        outputs=[header_status_text],
        queue=False,
        show_progress="hidden",
    )

    def _autoload_tick(file_path, settings_str, last_mtime):
        """Timer poll: reload in place only if the file changed on disk since last check."""
        try:
            mtime = Path(file_path).stat().st_mtime if file_path else 0.0
        except OSError:
            mtime = last_mtime
        if not file_path or mtime <= last_mtime:
            return (last_mtime,) + tuple([gr.update()] * len(load_outputs_no_picker))
        result = load_and_update_force(file_path, settings_str)
        return (mtime,) + tuple(result)

    def _on_autosave_toggle(enabled):
        """Autosave and Autoload are mutually exclusive."""
        print(f"[MODE] _on_autosave_toggle fired: enabled={enabled!r}")
        if not enabled:
            return gr.update(), gr.update(), "autosave off"
        return gr.update(value=False), gr.update(active=False), "autosave on"

    def _on_autoload_toggle(enabled, file_path):
        """Autosave and Autoload are mutually exclusive; seed the mtime baseline on enable."""
        global _autosave_last_time
        print(f"[MODE] _on_autoload_toggle fired: enabled={enabled!r} file_path={file_path!r}")
        if not enabled:
            return gr.update(), gr.update(active=False), gr.update(), "autoload off"
        try:
            mtime = Path(file_path).stat().st_mtime if file_path else 0.0
        except OSError:
            mtime = 0.0
        _autosave_last_time = time.time()
        return gr.update(value=False), gr.update(active=True), mtime, "autoload on"

    autosave_enabled.change(
        fn=_on_autosave_toggle,
        inputs=[autosave_enabled],
        outputs=[autoload_enabled, autoload_timer, header_status_text],
        queue=False,
        show_progress="hidden",
    )

    autoload_enabled.change(
        fn=_on_autoload_toggle,
        inputs=[autoload_enabled, current_file_path],
        outputs=[autosave_enabled, autoload_timer, _autoload_last_mtime, header_status_text],
        queue=False,
        show_progress="hidden",
    )

    autoload_timer.tick(
        fn=_autoload_tick,
        inputs=[current_file_path, settings_json, _autoload_last_mtime],
        outputs=[_autoload_last_mtime] + load_outputs_no_picker,
        show_progress="hidden",
    ).then(
        fn=_reselect_node_after_reload,
        inputs=[preview_code, selected_node],
        outputs=node_selector_outputs,
        show_progress="hidden", queue=False
    ).then(
        fn=_apply_editor_lora_choices_from_state,
        inputs=[lora_file_state],
        outputs=editor_lora_dropdowns,
        queue=False,
        show_progress="hidden",
    ).then(
        fn=sync_style_test_scene_dropdown,
        inputs=[preview_code],
        outputs=[style_test_context],
        queue=False,
        show_progress="hidden",
    ).then(
        fn=_file_fingerprint,
        inputs=[current_file_path],
        outputs=[_last_known_fingerprint],
        queue=False,
        show_progress="hidden",
    )

    def _watch_for_agent_mode(file_path, last_seen_writer):
        """Always-on, cheap poll (independent of the Autosave/Autoload checkboxes):
        notice when project.active_writer flips to "agent" and auto-switch into
        agent mode. One-directional — never auto-reverts, and won't re-fight a
        manual override once it's already reacted to a transition."""
        global _autosave_last_time
        import json
        if not file_path:
            return last_seen_writer, gr.update(), gr.update(), gr.update(), gr.update(), gr.update()
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                current_writer = json.load(f).get("project", {}).get("active_writer")
        except (OSError, ValueError):
            return last_seen_writer, gr.update(), gr.update(), gr.update(), gr.update(), gr.update()
        print(f"[MODE] _watch_for_agent_mode tick: current_writer={current_writer!r} last_seen_writer={last_seen_writer!r} file_path={file_path!r}")
        if current_writer == "agent" and last_seen_writer != "agent":
            print(f"[MODE] -> watcher detected transition to AGENT, auto-switching")
            _autosave_last_time = time.time()
            # Seed mtime at 0, not the current mtime — so the now-active autoload
            # timer's very next tick treats the file as changed and actually reloads it.
            return current_writer, gr.update(value=False), gr.update(value=True), gr.update(active=True), 0.0, "switched to agent mode"
        return current_writer, gr.update(), gr.update(), gr.update(), gr.update(), gr.update()

    mode_watch_timer.tick(
        fn=_watch_for_agent_mode,
        inputs=[current_file_path, _last_seen_active_writer],
        outputs=[_last_seen_active_writer, autosave_enabled, autoload_enabled, autoload_timer, _autoload_last_mtime, header_status_text],
        show_progress="hidden",
    )

    cancel_copy_btn.click(lambda: gr.update(visible=False), outputs=[copy_group])

    # PHASE 3: Save As - single function that saves and loads
    def save_as_and_load(save_path: str, settings_str: str, current_data: dict):
        """Save as new file then load it atomically"""
        data, filepath = cb_save_as(save_path, settings_str, current_data)
        return load_project_complete(filepath, settings_str, form, get_style_test_images)
    
    confirm_copy_btn.click(
        fn=save_as_and_load,
        inputs=[copy_path, settings_json, preview_code],
        outputs=load_outputs_with_picker,  # Include file_picker update to select new file
        queue=True,  # Sequential processing
        show_progress="minimal"
    ).then(
        fn=_apply_initial_mode_from_project,
        inputs=[preview_code, current_file_path],
        outputs=[autosave_enabled, autoload_enabled, autoload_timer, _autoload_last_mtime, _last_seen_active_writer, _last_known_fingerprint],
        queue=False,
        show_progress="hidden",
    ).then(
        fn=lambda: gr.update(visible=False),
        outputs=[copy_group],
        queue=False,
        show_progress="hidden"
    ).then(
        fn=lambda: gr.update(selected="project_tab"),
        outputs=[main_tabs],
        queue=False,
        show_progress="hidden"
    ).then(
        fn=sync_style_test_scene_dropdown,
        inputs=[preview_code],
        outputs=[style_test_context],
        queue=False,
        show_progress="hidden",
    )


    duplicate_proj_btn.click(
        lambda cur: (gr.update(visible=True), gr.update(value=Path(cur).stem if cur else "")),
        inputs=[current_file_path],
        outputs=[copy_group, copy_path]
    )
        
    master_form_inputs = [preview_code] + form.get_inputs()
    master_form_outputs = [preview_code]

    def _field_sync(json_path: str):
        """Sync one Settings control into preview_code using a local snapshot (single Gradio input)."""
        def sync(value):
            preview = _preview_snapshot[0]
            if preview is None:
                return {}
            return form.update_json_field(preview, json_path, value)

        return sync

    def _sync_video_model_family(family_label):
        """Sync video family to preview; normalize workflow when switching to Default."""
        preview = _preview_snapshot[0]
        if preview is None:
            return {}
        old_family = video_model_family(preview)
        data = form.update_json_field(preview, "project.video_model_family", family_label)
        new_family = video_model_family(data)
        if should_migrate_video_on_family_change(old_family, new_family):
            data = migrate_video_to_default_workflow(data)
        return data

    def _sync_image_model_family(family_label):
        """Sync family to preview; seed reference bindings on Default → Custom only."""
        preview = _preview_snapshot[0]
        if preview is None:
            return {}
        old_family = image_model_family(preview)
        data = form.update_json_field(preview, "project.image_model_family", family_label)
        new_family = image_model_family(data)
        if should_migrate_keyframes_to_custom_bindings(old_family, new_family):
            data = migrate_keyframes_to_custom_reference_bindings(data)
        return data

    for entry in form._registry:
        if not entry.get("is_input"):
            continue
        comp = entry["component"]
        if comp == file_picker:
            continue
        if entry["path"] == "project.image_model_family":
            comp.change(
                _sync_image_model_family,
                inputs=[comp],
                outputs=master_form_outputs,
                queue=True,
                show_progress="hidden",
            )
            continue
        if entry["path"] in (
            "project.video_model_family",
            "project.inbetween_generation.video_workflow_json",
        ):
            continue
        sync_fn = _field_sync(entry["path"])
        if isinstance(comp, (gr.Textbox, gr.Number)):
            comp.blur(sync_fn, inputs=[comp], outputs=master_form_outputs, queue=True, show_progress="hidden")
            comp.submit(sync_fn, inputs=[comp], outputs=master_form_outputs, queue=True, show_progress="hidden")
        elif isinstance(comp, (gr.Dropdown, gr.Radio, gr.Checkbox)):
            comp.change(sync_fn, inputs=[comp], outputs=master_form_outputs, queue=True, show_progress="hidden")
        else:
            comp.input(sync_fn, inputs=[comp], outputs=master_form_outputs, queue=True, show_progress="hidden")


    # Hidden Number fields (kf_seed_*, vid_* in visible=False column) sync via form.blur above.
    # Run-tab iteration controls are separate form.add() entries in build_run_tab.

    def _track_file_path(path):
        _file_path_snapshot[0] = path if path else ""

    current_file_path.change(
        fn=_track_file_path,
        inputs=[current_file_path],
        outputs=[],
        queue=False,
        show_progress="hidden",
    )

    def _apply_generation_buffer(result_data):
        return _conditionally_apply_update(
            result_data,
            _file_path_snapshot[0],
            _preview_snapshot[0],
        )

    generation_result_buffer.change(
        fn=_apply_generation_buffer,
        inputs=[generation_result_buffer],
        outputs=[preview_code],
    )

    def _workflow_name_for_negative_scan(data: dict, workflow_filename: str | None = None) -> str:
        if workflow_filename and str(workflow_filename).strip():
            return Path(str(workflow_filename).strip()).name
        if is_custom_image_family(data):
            return project_default_workflow_filename(data)
        return effective_default_workflow_filename(data)

    def _negative_field_visibility(preview, workflow_filename=None):
        data = preview if isinstance(preview, dict) else {}
        wf_name = _workflow_name_for_negative_scan(data, workflow_filename)
        caps = scan_workflow_file(wf_name)
        neg_vis = project_negative_visibility(
            caps,
            custom_family=is_custom_image_family(data),
        )
        return (
            gr.update(visible=neg_vis.show_keyframes_all),
            gr.update(visible=neg_vis.show_heal_all),
        )

    def _refresh_video_generation_defaults_ui(preview, video_workflow_dropdown=None):
        data = preview if isinstance(preview, dict) else {}
        default_family = is_default_video_family(data)
        if default_family:
            wf_name = DEFAULT_VIDEO_WORKFLOW_FILENAME
        else:
            wf_name = (video_workflow_dropdown or "").strip()
            if not wf_name:
                wf_name = effective_video_workflow_filename(data)
            else:
                wf_name = Path(video_workflow_dropdown_to_path(wf_name)).name
        scan = scan_video_workflow_file(wf_name if wf_name else None)
        vis = video_generation_defaults_visibility(scan)
        show_steps = (not default_family) and vis.show_video_steps
        return (
            gr.update(visible=show_steps, info=vis.video_steps_info),
            gr.update(visible=vis.show_video_fps, info=vis.video_fps_info),
        )

    def _refresh_video_model_family_ui(preview, video_workflow_dropdown=None):
        data = preview if isinstance(preview, dict) else {}
        custom = is_custom_video_family(data)
        default_family = is_default_video_family(data)

        if default_family:
            proj = data.setdefault("project", {})
            ib = proj.setdefault("inbetween_generation", {})
            default_path = str((WORKFLOWS_DIR / DEFAULT_VIDEO_WORKFLOW_FILENAME).resolve())
            if ib.get("video_workflow_json") != default_path:
                ib["video_workflow_json"] = default_path
            wf_update = gr.update(visible=False)
        else:
            wf_sel = stored_video_workflow_filename(data)
            wf_update = cb_refresh_video_workflow_dropdown(wf_sel)
            if isinstance(wf_update, dict):
                wf_update = {**wf_update, "visible": True}
            else:
                wf_update = gr.update(visible=True)

        steps_upd, fps_upd = _refresh_video_generation_defaults_ui(preview, video_workflow_dropdown)
        return wf_update, steps_upd, fps_upd

    def _refresh_image_model_family_ui(preview, workflow_filename=None):
        data = preview if isinstance(preview, dict) else {}
        custom = is_custom_image_family(data)
        default_family = is_default_image_family(data)

        if default_family:
            proj = data.setdefault("project", {})
            if proj.get("default_workflow_json") != DEFAULT_PROJECT_WORKFLOW_FILENAME:
                proj["default_workflow_json"] = DEFAULT_PROJECT_WORKFLOW_FILENAME

        if custom:
            wf_sel = project_default_workflow_filename(data)
            wf_update = cb_list_workflow_files(str(WORKFLOWS_DIR), wf_sel)
            if isinstance(wf_update, dict):
                wf_update = {**wf_update, "visible": True}
            else:
                wf_update = gr.update(visible=True)
        else:
            wf_update = gr.update(visible=False)

        neg_kf_vis, neg_heal_vis = _negative_field_visibility(preview, workflow_filename)
        asset_detail_update = gr.update(visible=custom)
        return (
            wf_update,
            gr.update(visible=custom),
            gr.update(visible=custom),
            gr.update(visible=custom),
            gr.update(visible=default_family),
            neg_kf_vis,
            neg_heal_vis,
            asset_detail_update,
            asset_detail_update,
            asset_detail_update,
            asset_detail_update,
            asset_detail_update,
            asset_detail_update,
            asset_detail_update,
            asset_detail_update,
            asset_detail_update,
        )

    _image_model_family_outputs = [
        default_workflow_dd,
        char_reference_save_btn,
        setting_reference_save_btn,
        style_reference_save_btn,
        kf_model_config_group,
        neg_kf,
        neg_heal,
        char_gen_prompt_group,
        char_model_settings_group,
        char_reflib_group,
        setting_gen_prompt_group,
        setting_model_settings_group,
        setting_reflib_group,
        style_gen_prompt_group,
        style_model_settings_group,
        style_reflib_group,
    ]

    preview_code.change(
        fn=_refresh_image_model_family_ui,
        inputs=[preview_code],
        outputs=_image_model_family_outputs,
        queue=False,
        show_progress="hidden",
    )

    image_model_family_radio.change(
        fn=_refresh_image_model_family_ui,
        inputs=[preview_code],
        outputs=_image_model_family_outputs,
        queue=False,
        show_progress="hidden",
    )

    default_workflow_dd.change(
        fn=_refresh_image_model_family_ui,
        inputs=[preview_code],
        outputs=_image_model_family_outputs,
        queue=False,
        show_progress="hidden",
    )

    _video_model_family_outputs = [
        video_workflow_dd,
        video_steps,
        video_fps,
    ]

    def _on_video_family_change(family_label, wf_dd):
        data = _sync_video_model_family(family_label)
        wf_upd, steps_upd, fps_upd = _refresh_video_model_family_ui(data, wf_dd)
        return data, wf_upd, steps_upd, fps_upd

    def _on_video_workflow_change(wf_dd):
        preview = _preview_snapshot[0]
        if preview is None:
            preview = {}
        data = form.update_json_field(
            preview, "project.inbetween_generation.video_workflow_json", wf_dd
        )
        steps_upd, fps_upd = _refresh_video_generation_defaults_ui(data, wf_dd)
        return data, steps_upd, fps_upd

    preview_code.change(
        fn=_refresh_video_generation_defaults_ui,
        inputs=[preview_code, video_workflow_dd],
        outputs=[video_steps, video_fps],
        queue=False,
        show_progress="hidden",
    )

    video_model_family_radio.change(
        fn=_on_video_family_change,
        inputs=[video_model_family_radio, video_workflow_dd],
        outputs=[preview_code, *_video_model_family_outputs],
        queue=False,
        show_progress="hidden",
    )

    video_workflow_dd.change(
        fn=_on_video_workflow_change,
        inputs=[video_workflow_dd],
        outputs=[preview_code, video_steps, video_fps],
        queue=False,
        show_progress="hidden",
    )

    def _lora_ceiling_interactive(enabled):
        return gr.update(interactive=bool(enabled))

    def _lora_ceilings_from_project(preview):
        data = preview if isinstance(preview, dict) else {}
        proj = data.get("project") or {}
        ln = proj.get("lora_normalization") or {}
        ib = proj.get("inbetween_generation") or {}
        return (
            gr.update(interactive=bool(ln.get("fg_enabled", True))),
            gr.update(interactive=bool(ln.get("bg_enabled", True))),
            gr.update(interactive=bool(ib.get("lora_normalization_enabled", False))),
        )

    for _toggle, _ceiling in (
        (norm_fg_en, norm_fg_max),
        (norm_bg_en, norm_bg_max),
        (norm_vid_en, norm_vid_max),
    ):
        _toggle.change(
            fn=_lora_ceiling_interactive,
            inputs=[_toggle],
            outputs=[_ceiling],
            queue=False,
            show_progress="hidden",
        )

    preview_code.change(
        fn=_lora_ceilings_from_project,
        inputs=[preview_code],
        outputs=[norm_fg_max, norm_bg_max, norm_vid_max],
        queue=False,
        show_progress="hidden",
    )

    comfyui_health_timer = gr.Timer(10.0, active=True)
    comfyui_health_timer.tick(
        fn=lambda pj, url: check_comfyui_status(pj, api_base=url),
        inputs=[preview_code, comfyui_api_base],
        outputs=[comfyui_status_md],
        queue=False,
        show_progress="hidden",
    )

    demo.load(
        fn=lambda: cb_list_json_files(settings.get("workspace_root", "./projects")),
        inputs=[],
        outputs=[file_picker],
        queue=False,
        show_progress="hidden"
    ).then(
        fn=lambda pj: check_comfyui_status(pj, api_base=settings.get("comfy", {}).get("api_base")),
        inputs=[preview_code],
        outputs=[comfyui_status_md],
        queue=False,
        show_progress="hidden",
    ).then(
        fn=lambda: (gr.update(interactive=True), gr.update(interactive=True)),
        outputs=[file_picker, node_selector],
        queue=False,
        show_progress="hidden"
    ).then(
        fn=sync_style_test_scene_dropdown,
        inputs=[preview_code],
        outputs=[style_test_context],
        queue=False,
        show_progress="hidden",
    )

    def on_refresh_click(pj_json):
        paths = get_style_test_images(pj_json)
        return paths, paths, f"Found {len(paths)} images."

    def on_upload_look(uploaded_file, pj_json):
        """Handles Look upload: copy to _looks folder, refresh gallery."""
        if not uploaded_file:
            return gr.update(), gr.update(), "Error: No file selected."
        
        try:
            # Extract project paths
            data = pj_json if isinstance(pj_json, dict) else {}
            output_root = data.get("project", {}).get("comfy", {}).get("output_root")
            project_name = data.get("project", {}).get("name")
            
            if not output_root or not project_name:
                return gr.update(), gr.update(), "Error: Project not loaded or invalid paths."
            
            # Prepare destination
            from pathlib import Path
            dest_dir = Path(output_root) / project_name / "_looks"
            dest_dir.mkdir(parents=True, exist_ok=True)
            
            # Get original filename (without path)
            original_name = Path(uploaded_file.name).stem
            
            # Use save_to_project_folder for auto-versioning
            msg, final_path = save_to_project_folder(uploaded_file.name, str(dest_dir), original_name)
            
            # Refresh gallery
            paths = get_style_test_images(pj_json)
            
            if final_path:
                return paths, paths, f"✓ Uploaded: {Path(final_path).name}"
            else:
                return paths, paths, msg
                
        except Exception as e:
            return gr.update(), gr.update(), f"Error uploading: {e}"


    def on_gallery_select(evt: gr.SelectData, paths):
        if not paths or evt.index >= len(paths):
            return "", "Error selecting image."
        
        # Force absolute path to help with long filename OS resolution
        selected = str(Path(paths[evt.index]).absolute())
        print(f"[GALLERY_SELECT] Path: {selected}")
        return selected, f"Selected: {Path(selected).name[:30]}..."
    
    def on_load_style_click(img_path, current_pj):
        print("\n" + "!"*30)
        print(f"!!! RECALL BUTTON TRIGGERED !!!")
        print(f"!!! Target Path: {img_path}")
        print("!"*30 + "\n")

        _recall_field_noops = 19

        if not img_path:
            return [gr.update()] * _recall_field_noops + ["No image selected."]

        print("passed image path")

        data, msg = recall_project_globals(img_path)
        if not data:
            return [gr.update()] * _recall_field_noops + [msg]
        
        # --- DEBUG BLOCK ---
        print(f"\n[STYLE_RECALL] Data keys found in image: {list(data.keys())}")
        for k in ['style_prompt', 'project.style_prompt', 'neg_global', 'project.negatives.global']:
             if k in data: print(f"  - Found {k}: {data[k]}")
        # -------------------

        new_pj = _deep_copy(current_pj)
        for key, val in data.items():
            path = f"project.{key}" if not key.startswith("project.") else key
            _set_by_path(new_pj, path, val)

        # Map flat metadata keys to UI return values
        # Use explicit None check to preserve falsey values like 0, False, ""
        def _val_or_noop(val):
            return gr.update() if val is None else val
        
        return (
            new_pj,
            _val_or_noop(data.get("width")), 
            _val_or_noop(data.get("height")), 
            _val_or_noop(data.get("style_prompt")),
            _val_or_noop(data.get("model")), 
            _val_or_noop(data.get("steps")), 
            _val_or_noop(data.get("cfg")),
            _val_or_noop(data.get("sampler")), 
            _val_or_noop(data.get("scheduler")),
            _val_or_noop(data.get("neg_global")), 
            _val_or_noop(data.get("neg_kf")), 
            _val_or_noop(data.get("neg_i2v")), 
            _val_or_noop(data.get("neg_heal")),
            _val_or_noop(data.get("lora_normalization.fg_enabled")), 
            _val_or_noop(data.get("lora_normalization.fg_max")),
            _val_or_noop(data.get("lora_normalization.bg_enabled")),
            _val_or_noop(data.get("lora_normalization.bg_max")),
            _val_or_noop(image_family_json_to_label(data.get("image_model_family", IMAGE_MODEL_FAMILY_DEFAULT))),
            _val_or_noop(data.get("default_workflow_json")),
            msg,
        )
    test_style_btn.click(
        fn=run_style_preview_click,
        inputs=[current_file_path, preview_code, style_test_context, settings_json],
        outputs=[
            style_save_status,
            style_test_image,
            style_test_log,
            generation_result_buffer,
            style_gallery,
            gallery_paths_state,
            status_recall,
        ],
        api_name="run_style_preview",
    )

    btn_refresh_gallery.click(
        fn=on_refresh_click,
        inputs=[preview_code],
        outputs=[style_gallery, gallery_paths_state, status_recall]
    )
    upload_look_btn.upload(
        fn=on_upload_look,
        inputs=[upload_look_btn, preview_code],
        outputs=[style_gallery, gallery_paths_state, status_recall]
    )
    style_gallery.select(
        fn=on_gallery_select,
        inputs=[gallery_paths_state],
        outputs=[selected_image_path_state, status_recall]
    )

    recall_outputs = [
        preview_code,
        width, height, style_tags, model_dd,
        kf_steps, kf_cfg, kf_sampler_name, kf_scheduler,
        neg_global, neg_kf, neg_i2v, neg_heal,
        norm_fg_en, norm_fg_max, norm_bg_en, norm_bg_max,
        image_model_family_radio,
        default_workflow_dd,
        status_recall,
    ]
    print(f"[INIT] recall_style_btn outputs count: {len(recall_outputs)}")

    recall_style_btn.click(
        fn=on_load_style_click,
        inputs=[selected_image_path_state, preview_code],
        outputs=recall_outputs,
    ).then(
        fn=_refresh_image_model_family_ui,
        inputs=[preview_code],
        outputs=_image_model_family_outputs,
        queue=False,
        show_progress="hidden",
    )

    style_save_btn.click(
        fn=form.update_json, # Sync UI to JSON state first
        inputs=master_form_inputs,
        outputs=[preview_code]
    ).then(
        fn=save_style_to_project,
        inputs=[style_test_image, preview_code],
        outputs=[style_save_status]
    ).then(
        fn=on_refresh_click, 
        inputs=[preview_code],
        outputs=[style_gallery, gallery_paths_state, status_recall]
    )


    # --- Sync Utilities Tab on Load ---
    def _sync_run_tab_from_json(json_data):
        import json
        # Handle both dict (direct from loader) and str (potential future use)
        if isinstance(json_data, dict):
            data = json_data
        else:
            try: data = json.loads(json_data) if json_data else {}
            except: data = {}

        pj_kf = data.get("project", {}).get("keyframe_generation", {})
        pj_vid = data.get("project", {}).get("inbetween_generation", {})
        
        return (
            pj_kf.get("image_iterations_default", 1),
            pj_kf.get("sampler_seed_start", 0),
            pj_kf.get("advance_seed_by", 1),
            pj_vid.get("video_iterations_default", 1),
            pj_vid.get("seed_start", 0),
            pj_vid.get("advance_seed_by", 1)
        )

    # --- AUTOMATED AUTOSAVE REGISTRATION ---
    def register_autosave_triggers(blocks_env):
        # Recursively check children
        children = getattr(blocks_env, "children", {})
        if isinstance(children, dict):
            child_list = children.values()
        else:
            child_list = children

        for component in child_list:
            # Trigger on any Button marked as 'primary'
            if isinstance(component, gr.Button) and getattr(component, "variant", None) == "primary":
                # Skip the save button itself and creation buttons to avoid issues/recursion
                if component.value not in ["Save", "New Project", "Create", "Cancel", "Generate Preview", "Generate Test"]:
                    component.click(
                        fn=_trigger_autosave,
                        inputs=[current_file_path, preview_code, settings_json, autosave_enabled, _last_known_fingerprint],
                        outputs=[autosave_enabled, _last_known_fingerprint, header_status_text],
                        queue=False,
                        show_progress="hidden"
                    )
            
            # Trigger on node selector navigation (Editor tab left panel)
            if isinstance(component, gr.Radio) and getattr(component, "elem_id", None) == "outline_list":
                component.change(
                    fn=_trigger_autosave,
                    inputs=[current_file_path, preview_code, settings_json, autosave_enabled, _last_known_fingerprint],
                    outputs=[autosave_enabled, _last_known_fingerprint, header_status_text],
                    queue=False,
                    show_progress="hidden"
                )

            # Trigger on Tab selection (includes sub-tabs in helper files)
            if isinstance(component, gr.Tab):
                component.select(
                    fn=_trigger_autosave,
                    inputs=[current_file_path, preview_code, settings_json, autosave_enabled, _last_known_fingerprint],
                    outputs=[autosave_enabled, _last_known_fingerprint, header_status_text],
                    queue=False,
                    show_progress="hidden"
                )
                
            if hasattr(component, "children"):
                register_autosave_triggers(component)

    register_autosave_triggers(demo)
    # ----------------------------------------

def main():
    host = os.environ.get("GRADIO_SERVER_NAME", "0.0.0.0")
    port = int(os.environ.get("GRADIO_SERVER_PORT", "7860"))
 
    allowed_paths = []
    path_keys_from_settings = [
        "workspace_root",
        "models_root",
        "loras_root",
        "workflows_root",
        "comfyui_restart_script_path",
    ]
    
    for key in path_keys_from_settings:
        if path_str := settings.get(key):
            path = Path(path_str)
            if path.is_file():
                if os.path.isdir(path.parent):
                    allowed_paths.append(os.path.normpath(path.parent))
            elif path.is_dir():
                allowed_paths.append(os.path.normpath(path))

    comfy_output_path = None

    if settings:
        comfy_output_path = settings.get("comfy", {}).get("output_root")

    if not comfy_output_path:
        try:
            comfy_output_path = project_json_init.get("project", {}).get("comfy", {}).get("output_root")
        except Exception:
            pass

    if not comfy_output_path:
        comfy_output_path = DEFAULT_PROJECT["project"]["comfy"]["output_root"]

    if comfy_output_path and os.path.isdir(comfy_output_path):
        allowed_paths.append(os.path.normpath(comfy_output_path))
        
    unique_allowed_paths = sorted(list(set(allowed_paths)))
    
    print(f"[DEBUG] comfy_output_path = {comfy_output_path}")
    print(f"[DEBUG] allowed_paths = {unique_allowed_paths}")
    print(f"[DEBUG] settings type = {type(settings)}")
    print(f"[DEBUG] settings.get('comfyui') = {settings.get('comfyui') if settings else 'settings is None'}")
    print(f"[DEBUG] settings = {settings}")

    _check_config_on_startup()
    PROJECT_ROOT = Path(__file__).parent.parent

    app_py = Path(__file__).resolve()
    print(f"[STARTUP] app module: {app_py}")
    print(f"[STARTUP] style preview API: /run_style_preview (not /handle_style_test)")

    inbrowser = os.environ.get("GRADIO_INBROWSER", "").strip().lower() in ("1", "true", "yes")
    demo.launch(
        server_name=host,
        server_port=port,
        allowed_paths=unique_allowed_paths,
        favicon_path=str(PROJECT_ROOT / "icon.png"),
        inbrowser=inbrowser,
    )


if __name__ == "__main__":
    main()