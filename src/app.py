# app.py
import os, json, time, threading
from datetime import datetime
import gradio as gr
from gradio.themes.utils import colors
from pathlib import Path
from typing import Any
from form_manager import ProjectFormRegistry


from helpers import (
    APP_TITLE, ensure_settings, DEFAULT_PROJECT, normalize_project_shape, _deep_copy,
    cb_create_new_project, cb_open_file,
    cb_list_json_files, cb_list_model_files, cb_list_workflow_files, cb_list_pose_files, cb_refresh_all_lists,
    cb_save_project, cb_save_as, cb_save_settings, flush_gradio_cache, 
    refresh_pose_components, DUR_CHOICES, get_project_poses_dir, _set_by_path,
    load_project_complete, save_to_project_folder
)

from curate_helpers import build_curate_tab, curate_refresh
from editor_helpers import build_editor_tab, _eh_node_selected, _project_len_text
from assets_helpers import build_assets_tab, _inject_lora_simple
from helpers import get_project_poses_dir, get_pose_gallery_list
from run_helpers import build_run_tab, check_comfyui_status


from test_gen_helpers import (
    handle_style_test,
    handle_style_asset_test,
    handle_setting_test,
    recall_project_globals,
    save_style_to_project,
    get_style_test_images,list_style_test_options
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

def _trigger_autosave(file_path, project_data, settings_str):
    """Helper to trigger the existing save logic with debouncing."""
    global _autosave_last_time
    
    # Guard against incomplete inputs during initialization
    if not file_path or not project_data:
        print(f"[AUTOSAVE] Skipped - no file/data")
        return
    
    with _autosave_lock:
        now = time.time()
        if now - _autosave_last_time < _AUTOSAVE_DEBOUNCE_SEC:
            print(f"[AUTOSAVE] Debounced")
            return
        _autosave_last_time = now
    
    print(f"[AUTOSAVE] Saving on tab switch: {file_path}")
    cb_save_project(file_path, project_data, settings_str)

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
    padding-top: 12px !important; 
}
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
    margin-bottom: -10px !important; /* Pull tabs up */
    padding-top: 4px !important;
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
    margin-top: -12px !important; /* Pull up to stack closely under title */
    font-family: monospace;
    font-size: 0.85rem;
    opacity: 0.7;
}
/* --- Final Header Desktop Alignment --- */
#header-utility-col {
    min-width: 280px !important;
    flex-grow: 0 !important;
}

/* --- Final Header Desktop Alignment --- */
#header-utility-col {
    min-width: 280px !important;
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
    margin-top: 10px !important;
    text-align: left !important;
    font-size: 0.85rem;
    white-space: nowrap;
    flex-grow: 1 !important; /* Allows text to occupy left space */
}

#header-refresh-btn {
    margin-top: 0px !important;
    width: 85px !important;
    flex-grow: 0 !important; /* Prevents button from stretching */
}


/* Ensure the Refresh button and Status sit tight together */
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
            gr.Markdown(f"### {APP_TITLE} <small>v 1.0</small>", elem_id="app-title")
            project_name_header = gr.Markdown("", elem_id="project-path-display")
        
        # Column 2: Utility cluster
        with gr.Column(elem_id="header-utility-col"):
            with gr.Row(elem_classes=["header-utility-row"]):
                comfyui_status_md = gr.Markdown(elem_id="status_indicator")
                refresh_all_btn = gr.Button("Refresh Data", variant="secondary", size="sm", elem_id="header-refresh-btn", visible=False)


    # Shared state
    settings_json     = gr.State(value=settings_json_init)
    current_file_path = gr.State(value="")
    generation_result_buffer = gr.State(value={})
    # PHASE 3: Removed json_load_buffer and temp_file_path_buffer - no longer needed
    lora_file_state   = gr.State(value=["__initializing__"])



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
            with gr.Accordion("Generation Defaults", open=True, visible=False,elem_classes=["themed-accordion", "proj-theme"]) as project_basics_accordion:
                with gr.Row():
                    with gr.Column():
                        vid_dur = form.add("project.inbetween_generation.duration_default_sec", 
                            gr.Radio(DUR_CHOICES, label="Default In-Between Length (seconds)"), 
                            default="5", to_ui=_dur_to_choice, to_json=int)
                    with gr.Column():
                        vid_express = form.add("project.inbetween_generation.express_video", 
                            gr.Checkbox(label="Rough Draft", info="Low fidelity but fast for initial validation"), 
                            default=False)
                
                # gr.Markdown("### LoRA Normalization")
                with gr.Row():
                    # Video LoRAs
                    with gr.Column():
                        # gr.Markdown("**Video LoRAs**")
                        form.add("project.inbetween_generation.lora_normalization_enabled", 
                            gr.Checkbox(label="Normalize Video LoRAs"), default=False, to_json=bool)
                        form.add("project.inbetween_generation.lora_normalization_max", 
                            gr.Number(label="Max Saturation", info="LoRA weight ceiling (prevents overpowering)", value=1.5), 
                            default=1.5, to_json=float)
                    
                    # FG (Character) LoRAs
                    with gr.Column():
                        # gr.Markdown("**FG (Character)**")
                        norm_fg_en = form.add("project.lora_normalization.fg_enabled", 
                            gr.Checkbox(label="Normalize Foreground LoRAs (Characters)"), default=True, to_json=bool)
                        norm_fg_max = form.add("project.lora_normalization.fg_max", 
                            gr.Number(label="Max Saturation", info="LoRA weight ceiling (prevents overpowering)", value=1.5), 
                            default=1.5, to_json=float)
                    
                    # BG (Style/Set) LoRAs
                    with gr.Column():
                        # gr.Markdown("**BG (Style/Set)**")
                        norm_bg_en = form.add("project.lora_normalization.bg_enabled", 
                            gr.Checkbox(label="Normalize Background LoRAs (Styles and Locations)"), default=True, to_json=bool)
                        norm_bg_max = form.add("project.lora_normalization.bg_max", 
                            gr.Number(label="Max Saturation", info="LoRA weight ceiling (prevents overpowering)", value=1.5), 
                            default=1.5, to_json=float)




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

                    
                        with gr.Group():
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
                vid_wf_json = form.add("project.inbetween_generation.video_workflow_json", gr.Textbox(), default="")
                vid_prompt_template = form.add("project.inbetween_generation.prompt_template", gr.Textbox(), default="")
                vid_seed_target = form.add("project.inbetween_generation.seed_target_title", gr.Textbox(), default="IterKSampler")
                vid_seed_exclude = form.add("project.inbetween_generation.seed_exclude_title", gr.Textbox(), default="WanFixedSeed")    



            with gr.Accordion("JSON", open=False, visible=False):
                gr.Markdown("**Current Project (JSON preview)**")
                json_renderer = gr.Code(language="json", 
                    value=json.dumps(project_json_init, indent=2, ensure_ascii=False), 
                    visible=True, elem_id="json_renderer")
                preview_code = gr.State(value=project_json_init)
                
                preview_code.change(
                    fn=lambda x: json.dumps(x, indent=2, ensure_ascii=False),
                    inputs=[preview_code],
                    outputs=[json_renderer],
                    queue=False,
                    show_progress="hidden"
                )

            # Hidden LoRA dropdown (kept for compatibility)
            style_lora_dd = gr.Dropdown(label="Add LoRA", choices=[], interactive=True, visible=False)

            file_op_outputs = [preview_code, current_file_path]

        # ---------------------- Assets Tab ----------------------
        with gr.TabItem("Assets", interactive=False) as assets_tab:
            # pose_gallery, poses_dir_state, char_inject_dd, setting_inject_dd, style_inject_dd = build_assets_tab(preview_code, settings_json, features)
            pose_gallery, poses_dir_state, char_inject_dd, setting_inject_dd, style_inject_dd = build_assets_tab(preview_code, settings_json, current_file_path, features)
        # ---------------------- Editor Tab ----------------------
        with gr.TabItem("Editor", id="editor_tab", interactive=False) as editor_tab:
            # kf_workflow_json, kf_pose, vid_lora, node_selector, node_selector_outputs, seq_lora, kf_pose_gallery, kf_lora = build_editor_tab(
            kf_workflow_json, kf_pose, vid_lora, node_selector, node_selector_outputs, seq_lora, kf_pose_gallery, kf_lora, proj_len = build_editor_tab(
        # with gr.TabItem("Editor", id="editor_tab", interactive=False) as editor_tab:
        #     kf_workflow_json, kf_pose, vid_lora, node_selector, node_selector_outputs, seq_lora, kf_pose_gallery = build_editor_tab(
                preview_code, 
                settings_json, 
                current_file_path, 
                generation_result_buffer, 
                features
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

    master_refresh_inputs = [workspace_dir, models_dir, loras_dir, preview_code, model_dd, gr.State(None), kf_workflow_json, kf_pose, file_picker]
    master_refresh_outputs = [file_picker, model_dd, lora_file_state, kf_workflow_json, refresh_sink, kf_pose_gallery]





    curate_tab.select(
        fn=_trigger_autosave,
        inputs=[current_file_path, preview_code, settings_json],
        outputs=[]
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

    assets_tab.select(
        fn=_trigger_autosave,
        inputs=[current_file_path, preview_code, settings_json],
        outputs=[]
    ).then(
        fn=_refresh_pose_gallery_on_assets_tab,
        inputs=[preview_code],
        outputs=[pose_gallery]
    )

    editor_tab.select(
        fn=_trigger_autosave,
        inputs=[current_file_path, preview_code, settings_json],
        outputs=[]
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



    all_lora_consumers = [     
        char_inject_dd,     
        setting_inject_dd,  
        style_inject_dd,    
        style_lora_dd,      
        vid_lora,           
        seq_lora,
        kf_lora            
    ]

    def _broadcast_loras(lora_list):
        data = lora_list if isinstance(lora_list, list) else []
        return [gr.update(choices=data) for _ in all_lora_consumers]

    def _get_lora_list_only(path):
        u = cb_list_model_files(path)
        return u.get("choices", []) if isinstance(u, dict) else []

    demo.load(
        fn=_get_lora_list_only,
        inputs=[loras_dir],
        outputs=[lora_file_state],
        queue=False,
        show_progress="hidden"
    ).then(
        fn=_broadcast_loras,
        inputs=[lora_file_state],
        outputs=all_lora_consumers,
        queue=False,
        show_progress="hidden"
    )



    # Consolidated Global Refresh Wiring
    refresh_all_btn.click(
        fn=cb_refresh_all_lists,
        inputs=master_refresh_inputs,
        outputs=master_refresh_outputs
    ).then(
        fn=_get_lora_list_only,
        inputs=[loras_dir],
        outputs=[lora_file_state],
        queue=False,
        show_progress="hidden"
    ).then(
        fn=_broadcast_loras,
        inputs=[lora_file_state],
        outputs=all_lora_consumers,
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
    

    def load_and_update(filepath: str, settings_str: str, force: bool = False):
        """Wrapper for direct loads (file_picker.change, reload_btn) - no file_picker update
        
        Args:
            filepath: Path to project file (may be just filename from dropdown)
            settings_str: Settings JSON string
            force: If True, bypass debounce (for reload button)
        """
        import time
        
        print(f"[LOAD_WRAPPER] Called with filepath={filepath}, force={force}")
        
        # CRITICAL: Guard against None filepath during initialization
        if filepath is None:
            print(f"[LOAD_WRAPPER] Skipping load - filepath is None (initialization)")
            return [gr.update()] * len(load_outputs_no_picker)  # Return no-op updates
        
        # RESOLVE FULL PATH: If filepath is just a filename, prepend workspace_root
        from pathlib import Path
        filepath_path = Path(filepath)
        if not filepath_path.is_absolute() and not filepath_path.parent.name:
            # Just a filename like "test_complex.json" - resolve to workspace
            workspace = settings.get("workspace_root", "./samples")
            filepath = str(Path(workspace) / filepath)
            print(f"[LOAD_WRAPPER] Resolved filename to full path: {filepath}")
            
            # DEBOUNCING: Skip if we just loaded this file within last 1 second (unless forced)
            current_time = time.time()
            if not force and (_last_load_cache["filepath"] == filepath and 
                            current_time - _last_load_cache["timestamp"] < 1.0):
                print(f"[LOAD_WRAPPER] DEBOUNCED - Already loaded {filepath} {current_time - _last_load_cache['timestamp']:.2f}s ago")
                return [gr.update()] * len(load_outputs_no_picker)  # Return no-op updates
            
            try:
                result = load_project_complete(filepath, settings_str, form, get_style_test_images)
                print(f"[LOAD_WRAPPER] Got result with {len(result)} items")
                
                # Update debounce cache
                _last_load_cache["filepath"] = filepath
                _last_load_cache["timestamp"] = current_time

                form_count = len(form.get_outputs())
                file_picker_index = 2 + form_count  # preview_code + current_file_path + form_values
                
                # Convert to list and surgically align to 52
                temp_list = list(result)
                
                # Pop the file_picker based on index (expected at 36)
                if len(temp_list) > file_picker_index:
                    temp_list.pop(file_picker_index)

                # Sync to exactly 52 to match load_outputs_no_picker
                temp_list = temp_list[:52]
                while len(temp_list) < 52:
                    temp_list.append(gr.update())

                print(f"[LOAD_WRAPPER] Returning {len(temp_list)} items (Synced to 52)")
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
            temp_list = list(result)
            # Sync to 53 to match load_outputs_with_picker
            temp_list = temp_list[:53]
            while len(temp_list) < 53:
                temp_list.append(gr.update())
            return tuple(temp_list)
        except Exception as e:
            print(f"[LOAD_WRAPPER_PICKER] ERROR: {e}")
            import traceback
            traceback.print_exc()
            raise
    
    def load_and_update_force(filepath: str, settings_str: str):
        """Wrapper for reload button - bypasses debounce"""
        return load_and_update(filepath, settings_str, force=True)
    
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
    refresh_outputs_no_picker = [model_dd, lora_file_state, kf_workflow_json, refresh_sink, kf_pose_gallery]
    
    # Target: 52 components
    load_outputs_no_picker = (
        [preview_code, current_file_path] + 
        form_outputs + 
        [model_dd, lora_file_state, kf_workflow_json, refresh_sink, kf_pose_gallery] + 
        [project_name_header, poses_dir_state, pose_gallery, style_gallery] + 
        locked_ui_components
    )
    
    # Target: 53 components
    load_outputs_with_picker = (
        [preview_code, current_file_path] +
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
        fn=lambda: (gr.update(visible=False),gr.update(visible=True)),
        outputs=[new_file_group,file_picker_group],
        queue=False,
        show_progress="hidden"
    )

    def _select_first_node_after_load(project_dict):
        """After load, select the first sequence to populate the editor"""
        data = project_dict if isinstance(project_dict, dict) else {}
        seqs = data.get("sequences", {})
        if seqs:
            first_id = list(seqs.keys())[0]
            # Call _eh_node_selected directly with first_id as raw_value
            return _eh_node_selected(data, first_id, None)
        # Return no-ops if no sequences
        return tuple([gr.update()] * 67)

    file_picker.change(
        fn=load_and_update,
        inputs=[file_picker, settings_json],
        outputs=load_outputs_no_picker,
        queue=True,
        show_progress="minimal"
    ).then(
        fn=_select_first_node_after_load,
        inputs=[preview_code],
        outputs=node_selector_outputs,
        show_progress="hidden", queue=False
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
        fn=lambda: gr.update(visible=False),
        outputs=[copy_group],
        queue=False,
        show_progress="hidden"
    ).then(
        fn=lambda: gr.update(selected="project_tab"),
        outputs=[main_tabs],
        queue=False,
        show_progress="hidden"
    )


    duplicate_proj_btn.click(
        lambda cur: (gr.update(visible=True), gr.update(value=Path(cur).stem if cur else "")),
        inputs=[current_file_path],
        outputs=[copy_group, copy_path]
    )
        
    master_form_inputs = [preview_code] + form.get_inputs()

    master_form_outputs = [preview_code]

    for comp in form.get_inputs():
        # Skip file_picker to prevent triggering during demo.load()
        if comp == file_picker:
            continue
        
        if isinstance(comp, (gr.Textbox, gr.Number)):
            comp.blur(form.update_json, inputs=master_form_inputs, outputs=master_form_outputs, queue=False, show_progress="hidden")
            comp.submit(form.update_json, inputs=master_form_inputs, outputs=master_form_outputs, queue=False, show_progress="hidden")
        elif isinstance(comp, (gr.Dropdown, gr.Radio, gr.Checkbox)):
            comp.change(form.update_json, inputs=master_form_inputs, outputs=master_form_outputs, queue=False, show_progress="hidden")
        else:
            comp.input(form.update_json, inputs=master_form_inputs, outputs=master_form_outputs, queue=False, show_progress="hidden")


    


    # Note: img_iter, kf_seed_start etc were reassigned to the run_tab versions at lines 403+
    img_iter.input(lambda p, v: _manual_set(p, "project.keyframe_generation.image_iterations_default", v), inputs=[preview_code, img_iter], outputs=[preview_code], show_progress="hidden")
    kf_seed_start.input(lambda p, v: _manual_set(p, "project.keyframe_generation.sampler_seed_start", v), inputs=[preview_code, kf_seed_start], outputs=[preview_code], show_progress="hidden")
    kf_advance.input(lambda p, v: _manual_set(p, "project.keyframe_generation.advance_seed_by", v), inputs=[preview_code, kf_advance], outputs=[preview_code], show_progress="hidden")
    
    vid_iter.input(lambda p, v: _manual_set(p, "project.inbetween_generation.video_iterations_default", v), inputs=[preview_code, vid_iter], outputs=[preview_code], show_progress="hidden")
    vid_seed_start.input(lambda p, v: _manual_set(p, "project.inbetween_generation.seed_start", v), inputs=[preview_code, vid_seed_start], outputs=[preview_code], show_progress="hidden")
    vid_advance.input(lambda p, v: _manual_set(p, "project.inbetween_generation.advance_seed_by", v), inputs=[preview_code, vid_advance], outputs=[preview_code], show_progress="hidden")



    style_lora_dd.change(
        fn=_inject_lora_simple,
        inputs=[style_tags, style_lora_dd],
        outputs=[style_tags, style_lora_dd],
        queue=False,
        show_progress="hidden"
    )

    generation_result_buffer.change(
        fn=_conditionally_apply_update,
        inputs=[generation_result_buffer, current_file_path, preview_code],
        outputs=[preview_code]
    )

    comfyui_health_timer = gr.Timer(10.0, active=True)

    comfyui_health_timer.tick(
        fn=lambda pj: check_comfyui_status(pj, api_base=settings.get("comfy", {}).get("api_base")),
        inputs=[preview_code],
        outputs=[comfyui_status_md],
        queue=False,
        show_progress="hidden"
    )
    

    demo.load(
        fn=lambda: cb_list_json_files(settings.get("workspace_root", "./samples")),
        inputs=[],
        outputs=[file_picker],
        queue=False,
        show_progress="hidden"
    ).then(
        fn=lambda pj: check_comfyui_status(pj, api_base=settings.get("comfy", {}).get("api_base")),
        inputs=[preview_code],
        outputs=[comfyui_status_md],
        queue=False,
        show_progress="hidden"

    ).then(
        fn=lambda: (gr.update(interactive=True), gr.update(interactive=True)),
        outputs=[file_picker, node_selector],
        queue=False,
        show_progress="hidden"
    )

    preview_code.change(
        fn=lambda pj: gr.update(choices=list_style_test_options(pj)),
        inputs=[preview_code],
        outputs=[style_test_context], 
        queue=False,
        show_progress="hidden"
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

        if not img_path:
            return [gr.update()] * 17 + ["No image selected."]
        
        print("passed image path")
        
        data, msg = recall_project_globals(img_path)
        if not data:
             return [gr.update()] * 17 + [msg]
        
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
            msg
        )
    test_style_btn.click(
        fn=lambda: "",
        inputs=[],
        outputs=[style_save_status]
    ).then(
        fn=cb_save_project,
        inputs=[current_file_path, preview_code, settings_json],
        outputs=[]
    ).then(
        fn=handle_style_test,
        inputs=[preview_code, current_file_path, style_test_context],
        outputs=[style_test_image, style_test_log, generation_result_buffer]
    ).then(
        fn=on_refresh_click,
        inputs=[preview_code],
        outputs=[style_gallery, gallery_paths_state, status_recall]
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
        status_recall
    ]
    print(f"[INIT] recall_style_btn outputs count: {len(recall_outputs)}")

    recall_style_btn.click(
        fn=on_load_style_click,
        inputs=[selected_image_path_state, preview_code],
        outputs=recall_outputs
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
                if component.value not in ["Save", "New Project", "Create", "Cancel"]:
                    component.click(
                        fn=_trigger_autosave,
                        inputs=[current_file_path, preview_code, settings_json],
                        outputs=[],
                        queue=False,
                        show_progress="hidden"
                    )
            
            # Trigger on node selector navigation (Editor tab left panel)
            if isinstance(component, gr.Radio) and getattr(component, "elem_id", None) == "outline_list":
                component.change(
                    fn=_trigger_autosave,
                    inputs=[current_file_path, preview_code, settings_json],
                    outputs=[],
                    queue=False,
                    show_progress="hidden"
                )

            # Trigger on Tab selection (includes sub-tabs in helper files)
            if isinstance(component, gr.Tab):
                component.select(
                    fn=_trigger_autosave,
                    inputs=[current_file_path, preview_code, settings_json],
                    outputs=[],
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


    demo.launch(
        server_name=host,
        server_port=port,
        allowed_paths=unique_allowed_paths,
        favicon_path=str(PROJECT_ROOT / "icon.png")
        # favicon_path="icon.png"
    )


if __name__ == "__main__":
    main()