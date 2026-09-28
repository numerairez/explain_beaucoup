#!/usr/bin/env python3
"""Explain Beaucoup - an interactive analytical workspace.

    python main.py                    # the bundled demo model
    python main.py path/to/model.yaml # your own

See `python -m explain_beaucoup --help` for init / check / run.
"""

from __future__ import annotations

import sys
from pathlib import Path

DEFAULT_MODEL = Path(__file__).parent / "models" / "sales.yaml"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    from explain_beaucoup.cli import launch
    return launch(Path(argv[0]) if argv else DEFAULT_MODEL)


if __name__ == "__main__":
    raise SystemExit(main())
