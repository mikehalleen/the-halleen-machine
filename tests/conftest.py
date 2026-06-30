import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"

for path in (ROOT, SRC, SCRIPTS):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

if "gradio" not in sys.modules:
    gradio_stub = types.ModuleType("gradio")
    gradio_stub.components = types.SimpleNamespace(Component=object)
    gradio_stub.update = lambda **kwargs: kwargs
    gradio_stub.Info = lambda *args, **kwargs: None
    sys.modules["gradio"] = gradio_stub
elif not hasattr(sys.modules["gradio"], "update"):
    sys.modules["gradio"].update = lambda **kwargs: kwargs
