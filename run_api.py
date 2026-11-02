#!/usr/bin/env python3
"""Launch the HTTP API of the parking reservation system (CP1 walking skeleton).

Usage: python3 run_api.py [path/to/parking.db] [--host 127.0.0.1] [--port 8000]
"""
import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from parking.http_api import main  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("db", nargs="?", default="parking.db", help="SQLite file, shared with the GUI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    main(args.db, args.host, args.port)
