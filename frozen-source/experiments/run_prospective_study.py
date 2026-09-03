"""Archive-gated entry point for the sealed prospective semantic batch."""

from __future__ import annotations

import sys

from marlrefine.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["prospective", *sys.argv[1:]]))
