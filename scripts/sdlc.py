#!/usr/bin/env python3
"""Entry point for the sdlc plugin's scripts. See INSTRUCTIONS.md."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sdlc.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
