"""Pytest setup: mock heavy app deps before importing builder (which pulls src/helpers)."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

for _mod in ("gradio", "gradio.components", "PIL", "PIL.Image", "PIL.PngImagePlugin"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()
