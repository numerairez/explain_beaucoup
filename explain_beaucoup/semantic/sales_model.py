"""The demo dataset's semantic model.

It is declared in `models/sales.yaml` like any team's model would be - this
module just loads it, so the bundled example and a customer's own set-up go
through exactly the same code path.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .loader import build
from .specs import SemanticModel

MODEL_PATH = Path(__file__).resolve().parents[2] / "models" / "sales.yaml"


def sales_model() -> SemanticModel:
    doc = yaml.safe_load(MODEL_PATH.read_text())
    model, _ = build(doc, MODEL_PATH.parent, load_data=False)
    return model
