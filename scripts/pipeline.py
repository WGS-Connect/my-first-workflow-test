#!/usr/bin/env python3
"""Single-channel audiobook production runner."""
from __future__ import annotations
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.channels.audiobook import produce
from src.errors import PipelineError

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Production-ready audiobook YouTube automation")
    parser.add_argument("--channel", choices=["audiobook"], default="audiobook")
    parser.add_argument("--test", action="store_true", help="bounded private test render")
    parser.add_argument("--root", default="work")
    args = parser.parse_args(argv)
    try:
        print(produce(root=args.root, test=args.test))
        return 0
    except PipelineError:
        raise

if __name__ == "__main__":
    raise SystemExit(main())
