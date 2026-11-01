# The Halleen Machine

**Version 2.0** (pre-release)
Asset management and generation layer for AI video projects on top of ComfyUI.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

Organize a project as a library of **bins** — each a recipe (workflow, prompt, references, LoRAs) plus the gallery of everything generated from it. Render image and video takes through your own ComfyUI, review them in the web UI, and optionally hand the whole loop to an AI agent through the same command API the UI uses.

THM 2.0 is a rewrite: the 1.x Gradio app is gone, replaced by a FastAPI server (`thm_server/`), a UI-free core (`thm_core/`), and a React web client (`web/`).

---

## Requirements

- Python 3.11+
- ComfyUI installed and running (its API reachable, usually `http://127.0.0.1:8188`)
- Node.js 18+ — **only** if you want to modify and rebuild the web UI. A built copy ships in `web/dist/`.

---

## Installation

```bash
# Clone repository
git clone https://github.com/mikehalleen/the-halleen-machine.git
cd the-halleen-machine

# Create virtual environment
python -m venv venv

# Activate it
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure: ComfyUI's API address and output folder, your projects folder
python setup.py

# Launch the server
python serve.py
```

Then open **http://127.0.0.1:8010**. The server also prints a LAN address for other devices, and the API docs live at `/docs`.

Windows shortcuts: `start_server.bat` (starts the server) and `start_server.ps1` (rebuilds the web client first, then starts; use `-NoBuild` to skip the build). Both use `venv`'s python when present.

### Configuration

`python setup.py` writes `config.toml` (see `config.toml.example` for every key). The ones that matter:

| Key | What |
|---|---|
| `[comfyui] api_base` | ComfyUI's API address |
| `[comfyui] output_root` | ComfyUI's output folder; THM reads renders from here |
| `[paths] workspace` | Where projects are stored (default `./projects`) |
| `[paths] loras` | ComfyUI's `models/loras`, for the LoRA picker |

---

## Workflows: bring your own

THM does not ship models and does not install them. It drives ComfyUI through **your** workflows: export any working ComfyUI graph in API format, give the nodes THM wants to control the titles described in `WORKFLOWS.md` (prompt, references, sampler, save, and so on), and drop the file where its kind belongs:

- `workflows_image/` — image workflows offered in the UI
- `workflows_video/` — video workflows offered in the UI
- `workflows/prompt_enhancer.json` — the local prompt-enhancer graph (optional)

Anything in those folders appears as a model choice; whatever models and custom nodes the graph loads are yours to have in ComfyUI (ComfyUI-Manager is the usual way to get missing nodes). The files already in those folders are working examples of the tagging (Klein, LTX 2.5, WAN 2.2, MiniMax); `samples/workflows_openincomfy/` has editor-format versions you can open in ComfyUI to see how they are wired.

---

## Agent (optional)

The repo ships a client-neutral **Agent Skill** — `thm-agent/skill/thm-agent/` — plus a CLI (`thm-agent/cli.py`) and a stdlib-only HTTP client (`thm-agent/thm_client.py`). An agent drives projects entirely through the server's command API: it never touches project files.

Install the skill wherever your agent client (Claude Code, Cursor, …) discovers skills:

```bash
python install_skill.py --dest <your client's skills dir>
```

`python setup.py` offers this as an optional step. With the server running, the CLI works on its own too:

```bash
python thm-agent/cli.py --server http://127.0.0.1:8010 projects
```

---

## Rebuilding the web UI

The built client in `web/dist/` is what `serve.py` serves. To change it:

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
