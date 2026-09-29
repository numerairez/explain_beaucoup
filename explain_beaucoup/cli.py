"""Command line: get a team from "here is our file" to a running workspace.

    explain-beaucoup init sales.csv --time order_date   # propose a model
    explain-beaucoup check model.yaml                   # verify it against data
    explain-beaucoup run   model.yaml                   # open the workspace
    explain-beaucoup run                                # pick a dataset + scope
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RED, YEL, DIM, BOLD, OFF = "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"
COLOR = {"error": RED, "warning": YEL, "info": DIM}


def _tty() -> bool:
    return sys.stdout.isatty()


def _paint(text: str, code: str) -> str:
    return f"{code}{text}{OFF}" if _tty() else text


# --------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> int:
    from .data.source import DataSourceError, profile_frame, read_table
    from .semantic.loader import ModelError, scaffold

    try:
        frame = read_table(args.data, sheet=args.sheet, separator=args.separator)
    except DataSourceError as exc:
        print(_paint(f"error: {exc}", RED), file=sys.stderr)
        return 2

    data_path = Path(args.data).resolve()
    out = Path(args.out) if args.out else data_path.with_suffix(".yaml")
    dataset = args.name or data_path.stem.replace("-", "_")

    print(f"{_paint('Read', BOLD)} {data_path.name}: {len(frame):,} rows x "
          f"{len(frame.columns)} columns\n")
    for p in profile_frame(frame):
        tag = {"time": "time     ", "dimension": "dimension",
               "measure": "measure  ", "ignore": "skipped  "}[p.role]
        colour = {"time": YEL, "dimension": "", "measure": "",
                  "ignore": DIM}[p.role]
        line = (f"  {_paint(tag, colour) if colour else tag}  "
                f"{p.name:<24} {p.distinct:>7} distinct   {DIM if _tty() else ''}"
                f"{p.reason}{OFF if _tty() else ''}")
        print(line)

    try:
        text = scaffold(frame, _relative(data_path, out.parent), dataset=dataset,
                        time_column=args.time)
    except ModelError as exc:
        print(_paint(f"\nerror: {exc}", RED), file=sys.stderr)
        return 2

    if out.exists() and not args.force:
        print(_paint(f"\nerror: {out} already exists (use --force)", RED),
              file=sys.stderr)
        return 2
    out.write_text(text)
    print(f"\n{_paint('Wrote', BOLD)} {out}")
    print(f"\nNext: open it and fill in the three things only you know —")
    print("  1. hierarchies (drill paths, coarse to fine)")
    print("  2. which metrics are ratios (they must never be summed)")
    print("  3. the landing view under `defaults`")
    print(f"\nThen: explain-beaucoup check {out}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    from .semantic.loader import ModelError, check, load_model_file

    try:
        loaded = load_model_file(args.model)
    except ModelError as exc:
        print(_paint(f"error: {exc}", RED), file=sys.stderr)
        return 2

    model, source = loaded.model, loaded.source
    print(f"{_paint('Model', BOLD)} {loaded.path.name}  "
          f"({model.dataset}: {len(model.dimensions)} dimensions, "
          f"{len(model.metrics)} metrics, {len(model.hierarchies)} hierarchies)")
    print(f"{_paint('Data ', BOLD)} {source.row_count:,} rows, "
          f"{source.native_grain}-grain, grains available: "
          f"{', '.join(model.grains)}\n")

    issues = check(model, source)
    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warning"]
    for i in issues:
        print("  " + _paint(str(i), COLOR.get(i.level, "")))

    if not model.hierarchies:
        print("\n  " + _paint(
            "[NOTE   ] hierarchies: none declared - structural drill "
            "(Region -> City) is unavailable. Dimensional breakdown still "
            "works.", YEL))
    print()
    if errors:
        print(_paint(f"{len(errors)} error(s) — the model will not run.", RED))
        return 1
    print(_paint(f"OK — model is usable"
                 f"{f' ({len(warnings)} warning(s))' if warnings else ''}.",
                 BOLD))
    if args.sample:
        _sample(model, source)
    return 0


def _sample(model, source) -> None:
    """Prove the model end to end without opening a window."""
    from .core.context import Context, TimeWindow
    from .engine.engine import AnalyticalEngine
    from .engine.explain import explain
    from .view import format as fmt

    engine = AnalyticalEngine(source, model)
    grain = model.first_grain()
    ctx = Context.new(model.dataset, model.default_metric,
                      TimeWindow.single(source.latest(model.time_grain),
                                        model.time_grain),
                      grain=grain)
    res = engine.execute(ctx)
    metric = model.metric(ctx.metric)
    print(f"\n{_paint('Sample', BOLD)} {metric.label} by "
          f"{model.label_of(grain[0] if grain else None)}, {ctx.time.label}")
    for row in res.rows[:6]:
        share = f"  {row.share:6.1%}" if row.share is not None else ""
        print(f"    {row.label:<28} {fmt.value(metric, row.value):>16}{share}")
    exp = explain(engine, model, ctx)
    if exp.evidence:
        print(f"\n{_paint('Explain', BOLD)} top evidence:")
        for ev in exp.top(3):
            print(f"    {ev.score:.2f}  [{ev.kind}] {ev.headline}")
    else:
        print(f"\n{_paint('Explain', BOLD)} no evidence passed the thresholds "
              f"(usually means too little history).")


def cmd_run(args: argparse.Namespace) -> int:
    return launch(Path(args.model) if args.model else None,
                  models_dir=Path(args.models) if args.models else None)


def launch(model_path: Path | None = None, *,
           models_dir: Path | None = None) -> int:
    """Open the workspace. Without a model, the dataset picker runs first."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication

    # WebEngine needs a shared GL context, and the attribute has to be set
    # before the QApplication exists. Qt6 scales for high DPI unconditionally,
    # so the two AA_*HighDpi* attributes Qt5 needed here are gone.
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts,
                              True)

    # Importing the WebEngine module must also happen pre-QApplication.
    import PyQt6.QtWebEngineWidgets  # noqa: F401

    from .semantic.catalog import default_models_dir
    from .semantic.loader import ModelError, load_model_file
    from .ui.main_window import MainWindow
    from .view.theme import THEMES

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Explain Beaucoup")

    if model_path is not None:
        try:
            loaded = load_model_file(model_path)
        except ModelError as exc:
            print(_paint(f"error: {exc}", RED), file=sys.stderr)
            return 2
    else:
        # The picker also decides the scope, so it hands back a model whose
        # data is already narrowed to whatever was chosen.
        from .ui.dataset_dialog import choose_dataset
        loaded = choose_dataset(models_dir or default_models_dir(),
                                THEMES["light"])
        if loaded is None:
            return 0

    app.setApplicationName(loaded.model.title or "Explain Beaucoup")
    window = MainWindow(loaded.source, loaded.model,
                        scope_label=loaded.scope_label)
    window.show()
    return app.exec()


def _relative(target: Path, start: Path) -> str:
    try:
        return str(target.relative_to(start))
    except ValueError:
        import os
        return os.path.relpath(target, start)


# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="explain-beaucoup",
        description="An interactive analytical workspace over your own data.")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="profile a data file and write a starter model")
    p.add_argument("data", help="csv / parquet / xlsx / json / feather")
    p.add_argument("--time", help="the time column (detected if omitted)")
    p.add_argument("--out", help="where to write the model (default: <data>.yaml)")
    p.add_argument("--name", help="dataset name")
    p.add_argument("--sheet", help="excel sheet name or index")
    p.add_argument("--separator", help="csv separator")
    p.add_argument("--force", action="store_true", help="overwrite the model file")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("check", help="validate a model against its data")
    p.add_argument("model")
    p.add_argument("--sample", action="store_true",
                   help="also compute a first view and an explanation")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("run", help="open the workspace (no model: pick one)")
    p.add_argument("model", nargs="?",
                   help="a model file; omit it to choose a dataset and its "
                        "scope in the picker")
    p.add_argument("--models", help="folder of models the picker lists "
                                    "(default: ./models)")
    p.set_defaults(func=cmd_run)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))
