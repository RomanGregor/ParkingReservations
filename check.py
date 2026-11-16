#!/usr/bin/env python3
"""One repeatable build-and-test check (C09): the same command locally and in CI.

Usage: python3 check.py [--log FILE]

1. records what is checked: commit (and whether the tree has local changes),
   Python, SQLite, OS;
2. "build": byte-compiles src/, tests/ and the scripts, so a syntax error
   fails here and not later in a demo;
3. runs every test in tests/ (unit, service, SQLite integration, HTTP over a
   real socket, concurrency with two connections, architecture rule) and
   lists the skipped ones with their reason;
4. exits 0 only if the build and all tests passed.

Standard library only (D1): there is nothing to install, so there is no
dependency lock either.
"""
import argparse
import compileall
import os
import platform
import sqlite3
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for s in self.streams:
            s.write(text)

    def flush(self):
        for s in self.streams:
            s.flush()


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown (not a git checkout)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log", help="also write the output to this file")
    args = parser.parse_args()
    log = open(args.log, "w", encoding="utf-8") if args.log else None
    out = Tee(sys.stdout, log) if log else sys.stdout
    try:
        dirty = git("status", "--porcelain", "--untracked-files=no")
        print(f"commit:  {git('rev-parse', 'HEAD')}{' (+ local changes)' if dirty else ''}", file=out)
        print(f"python:  {platform.python_version()} ({sys.executable})", file=out)
        print(f"sqlite:  {sqlite3.sqlite_version}", file=out)
        print(f"os:      {platform.platform()}", file=out)

        print("\n== build: byte-compile ==", file=out)
        built = all(compileall.compile_dir(os.path.join(ROOT, d), quiet=1) for d in ("src", "tests", "measurements"))
        built = built and all(compileall.compile_file(os.path.join(ROOT, f), quiet=1)
                              for f in ("run.py", "run_api.py", "check.py"))
        print("build:   OK" if built else "build:   FAILED", file=out)

        print("\n== tests ==", file=out)
        sys.path.insert(0, os.path.join(ROOT, "src"))
        suite = unittest.defaultTestLoader.discover(os.path.join(ROOT, "tests"))
        result = unittest.TextTestRunner(stream=out, verbosity=2).run(suite)

        print("\n== summary ==", file=out)
        print(f"tests run: {result.testsRun}, failures: {len(result.failures)}, "
              f"errors: {len(result.errors)}, skipped: {len(result.skipped)}", file=out)
        for test, reason in result.skipped:
            print(f"  skipped: {test.id()} ({reason})", file=out)
        ok = built and result.wasSuccessful()
        print(f"result:  {'PASS' if ok else 'FAIL'} (exit {0 if ok else 1})", file=out)
        return 0 if ok else 1
    finally:
        if log:
            log.close()


if __name__ == "__main__":
    sys.exit(main())
