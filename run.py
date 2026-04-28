#!/usr/bin/env python3
import sys
from pathlib import Path

# Add src/ to path so imports work
sys.path.insert(0, str(Path(__file__).parent / "src"))

from app import main
main()