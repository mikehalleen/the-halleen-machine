# The Halleen Machine

**Version 2.0.0** 
Asset management and generation layer for AI video projects on top of ComfyUI.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

Organize a project as a library of **bins**: each one a recipe (workflow, prompt, references, LoRAs) plus the gallery of everything generated from it. Render image and video takes through your own ComfyUI, review them in the web UI, cut them into sequences, and optionally hand the whole loop to an AI agent through the same command API the UI uses.

THM 2.0 is a rewrite: the 1.x Gradio app is gone, replaced by a FastAPI server (`thm_server/`), a UI-free core (`thm_core/`), and a React web client (`web/`).

---

## Read this first: what you bring

THM is a layer on top of ComfyUI, not a ComfyUI distribution. There is no installer and no hand-holding. This README plus your own LLM should get you there.

**You are responsible for:**

- **ComfyUI itself**: installing it, keeping it updated, and running it with its API reachable from THM.
- **Your workflows**: building graphs that work in ComfyUI, and the models and custom nodes they load. THM ships no models and installs no nodes.
- **Telling THM about your workflows**: tagging them so THM knows which nodes to drive (see [Workflows](#workflows-bring-your-own)).

THM's job starts once a workflow already renders correctly in ComfyUI.

---

## Requirements

- Python 3.11+
- ComfyUI, installed and running ([ComfyUI on GitHub](https://github.com/comfyanonymous/ComfyUI))
- `ffmpeg` and `ffprobe` on your PATH (video posters, clips, sequence exports)
- Node.js 18+, **only** if you want to modify and rebuild the web UI. A built copy ships in `web/dist/`.

---

## Installation

```bash
# Clone the repository
git clone https://github.com/mikehalleen/the-halleen-machine.git
cd the-halleen-machine

# Create and activate a virtual environment
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Create your config, then edit it (see Configuration below)
# Windows:
copy config.toml.example config.toml
# Linux/Mac:
cp config.toml.example config.toml

# Start the server
python serve.py
```

Then open **http://127.0.0.1:8010**. The server also prints a LAN address for other devices, and the API docs live at `/docs`.

`serve.py` options: `--port 8011`, `--host 127.0.0.1` (local only), `--projects-dir <folder>`.

Windows shortcuts: `start_server.bat` starts the server; `start_server.ps1` does the same and rebuilds the web client first if you ran `npm install` in `web/` (`-NoBuild` skips that, `-Port` changes the port). Both stop whatever is already on the port and use `venv`'s python when it exists.

### Configuration

`config.toml` sits next to `serve.py`. Every key is explained in `config.toml.example`. Restart the server after you change it.

| Key | Required | What |
|---|---|---|
| `[comfyui] api_base` | yes | ComfyUI's API address, e.g. `http://127.0.0.1:8188` |
| `[comfyui] output_root` | yes | The folder ComfyUI saves renders to. THM reads every finished image and video from here |
| `[paths] workspace` | | Where projects are stored (default `./projects`) |
| `[paths] loras` | | ComfyUI's `models/loras` folder, for the LoRA picker |
| `[comfyui] timeout_seconds`, `video_timeout_seconds` | | How long a render may run before THM gives up |
| `[advanced] restart_script` | | Your own script that restarts ComfyUI, so THM (and the agent) can restart a stuck one |

Use forward slashes in paths on every platform (`D:/ComfyUI/output`).

---

## ComfyUI setup

Install ComfyUI however you like (portable, desktop app, git clone). THM only needs two things from it:

1. **Its API.** Start ComfyUI and check that `api_base` answers: opening `http://127.0.0.1:8188` in a browser should show the ComfyUI editor. If ComfyUI runs on another machine, start it with `--listen` and point `api_base` at that machine's address.
2. **Its output folder on disk.** `output_root` must be the folder ComfyUI itself writes to (usually `ComfyUI/output`). If ComfyUI is on another machine, share that folder and point `output_root` at the share, because THM reads the files directly.

Partner (paid API) nodes need a Comfy API key: set the `THM_COMFY_API_KEY` environment variable before starting the server. Keys are never read from `config.toml`.

---

## Workflows: bring your own

THM drives ComfyUI through **your** workflows. A workflow THM can use is a ComfyUI graph saved in **API format** whose important nodes carry `THM-*` titles (`THM-Prompt`, `THM-SaveImage`, `THM-ImageReference`, `THM-Seed`, ...). The titles tell THM which nodes to write into. Everything untagged stays exactly as you baked it (model, steps, cfg, and so on).

Where they go:

- `workflows_image/` holds image workflows; each file shows up as a choice in the UI
- `workflows_video/` holds video workflows

Both folders ship **empty**: THM can't render anything until you add at least one workflow. New files show up without a restart. The agent can do the whole import for you (see Step 2a).

### Step 1: get it working in ComfyUI

Build or download the workflow, install its custom nodes (ComfyUI-Manager's "Install Missing Custom Nodes" is the usual way) and models, and **run it once** until the output is what you want. Don't involve THM until this works.

### Step 2a: let THM tag it (import)

THM can tag a workflow for you from that run. ComfyUI's history keeps both the API graph and the editor graph, so nothing gets converted by hand. With the THM server running:

```bash
# Your recent ComfyUI runs, newest first
python thm-agent/cli.py workflows runs

# Preview the tags THM proposes for the newest run, writing nothing
python thm-agent/cli.py workflows import my_workflow --dry-run

# Fix any wrong guesses, then write it
python thm-agent/cli.py workflows import my_workflow --tag 12=THM-ImageReference --untag 31
```

Useful flags: `--run <id>` picks a run other than the newest, `--png <file>` imports from a ComfyUI output PNG instead of history, `--kind image|video` overrides the guess, and `--replace` overwrites an existing name. If the graph has no built-in prompt-enhancer LLM node, the import asks for a system prompt for THM's Enhance button (`--system-prompt-file prompt.txt`). Write one yourself, or let the agent research the model and write it.

The import writes the tagged API graph to `workflows_image/` or `workflows_video/`, plus a tagged editor copy to `samples/workflows_openincomfy/THM-<name>.json` (created on first import). To adjust tags later, open that copy in ComfyUI, rename node titles, run it once, and import again with `--replace`. Titles you set yourself always win.

If you use the agent, just tell it: "I have a workflow working in Comfy, import it as my_workflow."

### Step 2b: tag it by hand

1. In ComfyUI, rename the nodes THM should control to the `THM-*` titles described in **[WORKFLOWS.md](WORKFLOWS.md)** (its checklist is the contract).
2. Export in API format (the ComfyUI menu: Workflow → Export (API)).
3. Drop the `.json` into `workflows_image/` or `workflows_video/`.

### Step 3: use it

Pick the workflow on a bin in the UI and render. If THM isn't driving a node you expected, the node isn't tagged (or the tag is wrong): check it against `WORKFLOWS.md`.

### The prompt enhancer (test it first)

The **Enhance** button rewrites a prompt with an LLM, using the system prompt stored in each workflow (`THM-EnhancerSystemPrompt`). It has two engines:

- **Local:** runs `workflows/prompt_enhancer.json` (the one workflow that ships) on your ComfyUI. It uses ComfyUI's core `TextGenerate` node and loads `qwen_3_8b_abliterated_v2_bf16.safetensors` through its `Load CLIP` node. **You probably need to download that model** (or a compatible Qwen 3 text encoder) into ComfyUI's `models/text_encoders/`. If you use a different file, change the name in the `Load CLIP` node. A ComfyUI too old to have `TextGenerate` needs an update.
- **Agent:** your connected agent writes the prompt instead. No model needed.

**Test the local engine before relying on it:** open `workflows/prompt_enhancer.json` in ComfyUI (drag it onto the canvas), type something into `THM-EnhancerUserPrompt`, and run it. If it fails there, it fails in THM too. Fix it in ComfyUI, export it in API format back over `workflows/prompt_enhancer.json`, and keep the `THM-Enhancer*` node titles.

---

## Agent (optional)

The repo ships client-neutral **Agent Skills** (`thm-agent/skill/thm-agent/`, plus the optional `thm-agent/skill/thm-performance/`), a CLI (`thm-agent/cli.py`), and a stdlib-only HTTP client (`thm-agent/thm_client.py`). The agent drives projects entirely through the server's command API: it never touches project files, and it talks to you in the UI's agent panel.

**Install the skill:** copy (or symlink) the skill folders into wherever your agent client looks for skills. For Claude Code that's `~/.claude/skills/` (all projects) or `.claude/skills/` inside this repo. Other clients have their own folder; check their docs.

**Run it:** start the server, then start your agent client in this repo folder and ask it to connect to THM. The CLI also works on its own:

```bash
python thm-agent/cli.py --server http://127.0.0.1:8010 projects
```

Claude Code users on Windows can run `.\start_server.ps1 -Agent` to also start `thm_runner/supervisor.py`, which keeps one headless agent attached to the server and restarts it when it exits (needs the `claude` CLI on your PATH).

`thm-tools/audio/` is an optional audio-QC tool the agent can use (transcripts, beats, levels). It runs in its own venv with large CPU-only models; install it only if you want it, following the comments in `thm-tools/audio/requirements.txt`.

---

## Rebuilding the web UI

`serve.py` serves the built client in `web/dist/`. To change it:

```bash
cd web
npm install
npm run build
```

`npm run dev` starts a Vite dev server on port 5173 that proxies API calls to the THM server on 8010.

---

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The suite tests THM itself on synthetic graphs. Changing the code is encouraged; keep it green.

Your workflows are yours to test. `tests/test_user_workflows.py` runs basic checks on everything in `workflows_image/` and `workflows_video/` (API format, links resolve, no misspelled `THM-*` tags, THM can read it and write a prompt). Run `pytest tests/test_user_workflows.py` after adding or re-tagging a workflow. For deeper, model-specific tests (reference wiring, start/end frames, samplers, partner nodes), hand your coding agent **[tests/WORKFLOW_TESTS.md](tests/WORKFLOW_TESTS.md)**: it has the prompt to paste and the brief.

---

## Learning More

**Tutorials and guides:**
https://www.youtube.com/halleen

---

## License

GNU Affero General Public License v3.0 (AGPL-3.0)

- ✅ Use, modify, and distribute freely
- ⚠️ Must disclose source code of modified versions
- ⚠️ Network use counts as distribution (must share source)

See [LICENSE](LICENSE) for full terms.
