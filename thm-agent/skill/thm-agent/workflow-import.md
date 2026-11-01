# Bringing in a workflow from ComfyUI

"I have a workflow working in Comfy, call it X" is `workflows import X`. The user never
tags nodes or exports by hand; you do it through the server, which reads ComfyUI's
history and writes both files. You never touch a workflow file yourself.

## What it needs

The workflow has **run once** in ComfyUI. A run leaves both graphs in ComfyUI's history:
the API graph THM renders with and the UI graph the user opens. Nothing is converted.

- `workflows runs` lists the user's recent ComfyUI runs, newest first (THM's own renders
  are left out): id, kind, models, output files. With no `--run`, the import takes the
  newest. When the user says "the one I just ran" that is it; when there are several
  candidates, read the models and outputs back to them and pick by those.
- No run in history (ComfyUI restarted, it was never queued): ask the user to run it once
  and tell you. `--png <output.png>` works too, from an output file saved by ComfyUI.
- A run listed with "no UI graph" was posted through the API: only the API file is written.

## The system prompt

Every import leaves a `THM-EnhancerSystemPrompt` node: THM's own Enhance button rewrites
prompts by it, and `guide <workflow>` prints it.

- **The graph has its own enhancer** (an LLM node rewriting the prompt before the encoder):
  the import takes its system prompt, takes the LLM branch out of the API graph, mutes it
  in the user's copy, and feeds the encoder the raw text again. THM's enhancer does the
  rewriting from now on. `--keep-builtin-enhancer` leaves the graph's flow in place when
  the user wants it.
- **It has none:** the import refuses with `system_prompt_needed` until you supply one.
  The user's import request is your permission to research: find the model (the loader's
  file name, the partner node's provider), search the web for its prompting guide, model
  card and release notes, and write a system prompt for it. Keep the shape of THM's
  default (`guide` on any workflow shows it): the opening line, THE ONE RULE (a formatter,
  not a writer: nothing invented), TAGS (every @-tag kept exactly), OUTPUT, and the final
  handoff paragraph word for word -- the local enhancer model derails on other shapes. Put
  what you learned in OUTPUT: the structure and order the model reads best, length, the
  vocabulary it responds to, what it ignores or mangles (negatives, quality boilerplate,
  camera terms). Save it to a scratch file and pass `--system-prompt-file`. Say which
  sources you used when you report.

## Run it

1. `workflows import X --dry-run` (add `--system-prompt-file f.txt` when needed). Read the
   report: the tag table (node, class, old title -> tag, why), the enhancer, the LoRA
   marker, what stays baked, the scan.
2. Fix what is wrong with `--tag NODE=THM-...` and `--untag NODE` and dry-run again. The
   rules are in `WORKFLOWS.md`; the usual calls: the second `LoadImage` is the end frame,
   not a reference; a size the user wants baked (`--untag`); two model chains (WAN 14B
   high/low) get `THM-Lora-High` / `THM-Lora-Low` by hand.
3. Run it without `--dry-run`. `--replace` overwrites an existing name -- only when the
   user said to.
4. Tell the user in a few lines: the name, what THM now drives (prompt, negative, frames,
   references, size, seed, LoRAs), what stays baked (model, steps, cfg), the enhancer
   outcome, and that the tagged copy to open in ComfyUI is
   `samples/workflows_openincomfy/THM-X.json`. Then `workflows` shows it, and it is ready
   for `set_bin_field workflow`.

## Hands-on users

A user who wants to check or change the tags opens the samples copy in ComfyUI, retitles
nodes, runs it, and says "update X". That is `workflows import X --replace` on the new run:
their `THM-*` titles always win, and only gaps are filled. The import never writes into
ComfyUI itself.

## Refusals

`no_runs`, `unknown_run`, `ambiguous_run` (give more of the id), `thm_run` (that run was
THM's own render), `workflow_exists` (ask before `--replace`), `system_prompt_needed`,
`no_save_node` (the graph has no Save Image / Save Video: the user adds one and runs it
again), `enhancer_woven_in` (the LLM's output feeds more than the prompt: import with
`--keep-builtin-enhancer`), `bad_name`, `unknown_node`, `bad_tag`, `comfy_unreachable`.
