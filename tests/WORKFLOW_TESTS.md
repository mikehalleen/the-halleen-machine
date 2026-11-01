# Testing your own workflows

THM ships no workflows, so it can't ship tests for any particular one. What ships:

- **`tests/test_user_workflows.py`** runs basic checks on every file you put in `workflows_image/` and `workflows_video/`: the file is in API format, its links all resolve, it has no misspelled `THM-*` tags, THM's own scan reads it, and THM has a prompt to write into. It also checks that the prompt enhancer can be used. It skips when the folders are empty. Run it after you add or re-tag a workflow:

  ```bash
  pytest tests/test_user_workflows.py
  ```

- **This file:** a brief for a coding agent (Claude Code, Cursor, Codex, ...) to write deeper tests for the workflows you actually use.

The rest of `tests/` covers THM itself on synthetic graphs and should always pass.

---

## Prompt to give your coding agent

> Read `tests/WORKFLOW_TESTS.md`, `WORKFLOWS.md`, and the workflows in `workflows_image/` and `workflows_video/`. For each workflow, write tests in `tests/test_my_workflows.py` that cover the features that workflow actually has, following the brief. Don't edit the workflow files or THM's code. Run `pytest tests/test_user_workflows.py tests/test_my_workflows.py` and get them passing. If a test shows the workflow is mis-tagged, tell me what to change in ComfyUI instead of hiding it.

---

## Brief for the coding agent

### What THM does with a workflow

THM never converts a graph. Before every render it takes the user's API-format graph, deep-copies it, finds nodes by their `THM-*` titles (or legacy titles and classes), and writes values into their inputs: prompt, negative, seed, steps, size, frame count/rate, start/end frames, reference images and videos, LoRA chains, and partner-node fields. It mutes or rewires reference branches nobody bound, strips its own metadata nodes, and posts the result to ComfyUI. Every one of those steps is a pure function on a dict, so it can be tested without ComfyUI.

The tagging contract is `WORKFLOWS.md`. Its checklists are the spec.

### Where the code is

| Module | Import as | What |
|---|---|---|
| `scripts/workflow_controls.py` | `import workflow_controls as wc` | Finding tagged nodes and every injection THM does. `tests/conftest.py` puts `scripts/` on `sys.path` |
| `thm_core/workflow_capabilities.py` | `from thm_core import workflow_capabilities as caps` | The capability scan (`scan_workflow_file`, `scan_video_workflow_file`, `load_workflow_dict`, `partner_hint_texts`) |
| `thm_core/assembler.py` | `from thm_core.assembler import workflow_capacity, derive_token_profile` | Reference slot counts and how prompts address references |
| `thm_core/enhancer.py` | `from thm_core import enhancer` | The per-workflow `THM-EnhancerSystemPrompt` and the Enhance graph |
| `thm_server/derive.py` | `from thm_server.derive import workflow_info` | The summary the UI shows for a workflow |
| `scripts/run_images.py`, `scripts/run_video.py` | `import run_images`, `import run_video` | The runners; `run_generation` is the end-to-end entry point |

### Rules

1. **Never talk to ComfyUI.** Load the JSON, `copy.deepcopy` it, call the function, assert on the resulting dict. For runner end-to-end tests, stub HTTP the way the existing `tests/test_run_generation.py` does.
2. **Find nodes by title or class, never by node id.** Node ids change every time the user re-exports. Use `wc.find_control_nodes(graph, wc.PROMPT)` (the control constants are at the top of `workflow_controls.py`) or a small helper that matches `node["_meta"]["title"]`.
3. **Skip when the workflow isn't installed,** so the suite still passes for someone with different workflows:

   ```python
   import copy, json
   from pathlib import Path
   import pytest
   import workflow_controls as wc

   ROOT = Path(__file__).resolve().parents[1]

   def my_workflow(name: str, kind: str) -> dict:
       path = ROOT / f"workflows_{kind}" / name
       if not path.is_file():
           pytest.skip(f"{kind} workflow {name} is not installed")
       return copy.deepcopy(json.loads(path.read_text(encoding="utf-8")))
   ```

4. **Don't edit `workflows_*` files from tests.** If a test needs a variant, change the deep copy, or copy the file to `tmp_path`.
5. **Test what this workflow has.** A start-frame-only video model has no end frame to test, and an image model without reference loaders has no reference wiring. Read the graph first.
6. **Copy patterns from the tests already here.** `tests/thm_core_fixtures.py` builds synthetic graphs (`node`, `image_workflow`, `video_workflow`, `workflows_dir`). `tests/test_workflow_controls.py`, `tests/test_partner_node.py` and `tests/test_run_generation.py` show how each function is called and what it returns.

### What to cover, by feature

THM's developers test these against their own workflows. Recreate the ones that apply to the user's.

**Every workflow: contract**
- The titles and classes this model needs are present: `THM-Prompt`, `THM-SaveImage` or `THM-SaveVideo`, plus `THM-NegativePrompt`, `THM-Seed`, `THM-Steps`, `THM-Checkpoint`, `THM-Lora` and size tags when the workflow is meant to expose them. Use `wc.find_control_nodes` per control.
- After injecting a prompt, seed and size, the right nodes changed and nothing else did. Diff the copy against the original.
- `wc.strip_prompt_metadata(graph)` leaves no link pointing at a removed node (this is already covered generically).
- `workflow_info(str(path), ROOT / "workflows")` reports what the UI should show: `kind`, `image_slots`, `video_slots`, `supports_start_frame`, `supports_end_frame`, `has_negative`, `has_lora`, `supports_draft`, `supports_size`.

**Image references** (workflows with `THM-ImageReference` loaders)
- `wc.discover_image_reference_slots(graph)` finds the expected number of slots, in wiring order.
- `wc.discover_reference_wiring_order(graph)` returns the same order on repeated calls (deterministic).
- `wc.apply_reference_injection(graph, project=..., id_conf=...)` with fewer references than slots mutes the empty loaders and rewires the `ReferenceLatent` stack so the sampler still gets a connected chain. Klein-style parallel branches must not run with an empty `LoadImage` (`wc.mute_inactive_reference_slots`).
- `wc.remap_reference_bindings_for_slots` maps old ordinal bindings onto node-id slots and round-trips.
- `wc.compose_reference_prelude` (if the model wants named references in the prompt) includes exactly the bound ones.
- `workflow_capacity(name, workflows_dir)` and `derive_token_profile` agree with how the model addresses references (by token, by order, or by partner-node field).

**Video frames** (workflows with `THM-StartFrame` / `THM-EndFrame` or a recognised generator)
- `wc.discover_video_frame_input_support(graph)` says start-only, end-only or both, matching the model.
- `wc.configure_video_frames(graph, clip_type, start, end)` for `"SE"` (start + end), `"SO"` (start only), `"OE"` (end only): the unused frame's loader is disconnected or cleared and the used ones carry the paths.
- `wc.apply_video_injection(graph, wc.VideoInjectionContext(frame_clip_type=..., start_frame_path=..., end_frame_path=..., fps=..., frame_count=...))` ends up in the same place.
- `wc.set_frame_count` and `wc.set_frame_rate` write the tagged `THM-FrameCount` / `THM-FrameRate` nodes, and the generator's `length`, if the model takes one.

**Video sampling**
- `wc.discover_video_capabilities(graph)` lists the sampler passes, seed targets and LoRA modes the workflow really has (single, or high/low for WAN 14B).
- `wc.set_video_seeds(graph, seed)` writes every pass that should vary and none that should stay fixed (`seed_exclude_title`).
- `wc.set_steps` / `wc.video_project_controls_steps` respect a `THM-Steps` scheduler. `wc.discover_workflow_baked_sigma_schedules` finds baked sigma lists (LTX-style), which THM must leave alone.
- If the workflow has a `THM-IsDraft` switch, draft and full-quality renders pick the right branch.

**Partner (API) nodes** (`THM-PartnerNode`, e.g. Kling, Seedance, MiniMax)
- `wc.unknown_thm_tags` reports nothing (a malformed title like `THM-PartnerNode.0 ...` silently disables the node).
- `wc.set_partner_node_fields(graph, ctx)` writes the prompt (and negative only when the node has one) and `duration` in whole seconds when the field exists, and never touches enum fields (ratio, resolution, mode).
- `wc.derive_partner_metadata(graph)` reads the ratio and resolution correctly, returns `None` without a partner node, and doesn't raise on odd values.
- `wc.inject_partner_frame_scalers(graph, w, h)` inserts one scaler per wired frame loader, is idempotent, and does nothing without a partner node or with an invalid size.
- `wc.deactivate_empty_reference_loaders` takes unbound reference loaders out of the graph.
- An end-to-end `wc.apply_video_injection` covers all of the above in one call.

**Prompt enhancer**
- The workflow's own `THM-EnhancerSystemPrompt`, if it has one, is what `enhancer.system_prompt_for(path, kind)` returns (`source == "workflow"`).
- The runners strip it: after `wc.strip_prompt_metadata`, no `THM-Enhancer*` node remains.

**Import** (`workflows import` from ComfyUI history)
- To test a re-import of one of the user's workflows, save one `/history` entry from ComfyUI as a fixture in `tests/fixtures/`, then follow `tests/test_thm_server_workflow_import.py`: post it through a fake ComfyUI transport and check the tags proposed and the two files written (into `tmp_path`, never the real folders).
