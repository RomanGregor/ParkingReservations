#!/usr/bin/env python3
"""Launch the parking reservation system with its GUI.

Usage: python3 run.py [path/to/parking.db]
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from parking.gui import main  # noqa: E402

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "parking.db")
