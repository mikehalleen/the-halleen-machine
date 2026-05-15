# The Halleen Machine

**Version 1.0.2**  
Workflow management system for AI video generation using ComfyUI.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

Organize AI video projects into structured timelines. Create reusable asset libraries for poses, characters, locations, and styles. Batch operations for generation, upscaling, and exports.


New in this version:
- Added WORKFLOWS.md and additional fixes to improve workflow switching 

---

## Requirements

- Python 3.11+ 
- ComfyUI installed and running


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

# Configure and download needed models and custom nodes
python setup.py

# Launch
python run.py --listen 0.0.0.0 --port 7860
```

---


## Learning More

**Tutorials and guides:** 
https://www.youtube.com/halleen

---


## Road Map

**Short term** 
Expand beyond the SDXL and Wan2.2 ecosystem
Batch quality of life improvements (status, task canceling, queue stability)
Minor UX improvements

**Long term**
Replace Gradio with another front end framework

---

## License

GNU Affero General Public License v3.0 (AGPL-3.0)

- ✅ Use, modify, and distribute freely
- ⚠️ Must disclose source code of modified versions
- ⚠️ Network use counts as distribution (must share source)

See [LICENSE](LICENSE) for full terms.
See [MODELS.md](MODELS) for model attribution.
