"""Batch image seed calculation for run_images."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_images import iteration_seed


def test_iteration_seed_first_image_with_zero_base():
    assert iteration_seed(0, 0, 1) == 0


def test_iteration_seed_zero_index_uses_base():
    assert iteration_seed(100, 0, 17) == 100


def test_iteration_seed_resume_index():
    assert iteration_seed(100, 3, 17) == 151


def test_iteration_seed_clamps_negative():
    assert iteration_seed(0, -1, 1) == 0
