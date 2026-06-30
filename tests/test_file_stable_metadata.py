"""Output file stability and atomic snapshot inject."""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from helpers import inject_the_machine_snapshot, wait_for_file_stable


def test_wait_for_file_stable_small_png():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "out.png"
        path.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        assert wait_for_file_stable(str(path), timeout_s=5.0, stable_polls=2)


def test_inject_snapshot_atomic_replace():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "look.png"
        path.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        snap = {"project_context": {"style_prompt": "test"}, "meta": {}}
        assert inject_the_machine_snapshot(str(path), snap)
        from PIL import Image

        with Image.open(path) as img:
            raw = img.info.get("the_machine_snapshot")
        assert raw
        assert json.loads(raw)["project_context"]["style_prompt"] == "test"
        assert not path.with_name(path.name + ".thm.tmp").exists()
