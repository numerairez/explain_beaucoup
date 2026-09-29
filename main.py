#!/usr/bin/env python3
"""Explain Beaucoup - an interactive analytical workspace.

    python main.py                    # pick a dataset from models/ and scope it
    python main.py path/to/model.yaml # straight into one model

See `python -m explain_beaucoup --help` for init / check / run.
"""

from __future__ import annotations

import sys
from pathlib import Path

MODELS_DIR = Path(__file__).parent / "models"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    from explain_beaucoup.cli import launch
    return launch(Path(argv[0]) if argv else None, models_dir=MODELS_DIR)


if __name__ == "__main__":
    raise SystemExit(main())
