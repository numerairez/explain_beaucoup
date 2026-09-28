"""Chart + UI design tokens.

Light and dark are each *selected* - the dark column is the same hues stepped
for the dark surface, not an automatic flip.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    name: str
    surface: str          # chart surface
    plane: str            # page plane behind the chart
    ink: str              # primary text
    ink_2: str            # secondary text
    muted: str            # axis / tick labels
    grid: str
    axis: str
    border: str
    series: tuple[str, ...]        # categorical slots, fixed order
    sequential: tuple[str, ...]    # one hue, light -> dark
    pos: str              # diverging positive pole
    neg: str              # diverging negative pole
    mid: str              # diverging neutral midpoint
    good: str
    critical: str
    warning: str
    selected: str         # ring / emphasis for the selected mark

    @property
    def is_dark(self) -> bool:
        return self.name == "dark"


LIGHT = Theme(
    name="light",
    surface="#fcfcfb", plane="#f9f9f7",
    ink="#0b0b0b", ink_2="#52514e", muted="#898781",
    grid="#e1e0d9", axis="#c3c2b7", border="rgba(11,11,11,0.10)",
    series=("#2a78d6", "#eb6834", "#1baf7a", "#eda100",
            "#e87ba4", "#008300", "#4a3aa7", "#e34948"),
    sequential=("#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5",
                "#2a78d6", "#256abf", "#184f95", "#0d366b"),
    pos="#2a78d6", neg="#e34948", mid="#f0efec",
    good="#0ca30c", critical="#d03b3b", warning="#fab219",
    selected="#0b0b0b",
)

DARK = Theme(
    name="dark",
    surface="#1a1a19", plane="#0d0d0d",
    ink="#ffffff", ink_2="#c3c2b7", muted="#898781",
    grid="#2c2c2a", axis="#383835", border="rgba(255,255,255,0.10)",
    series=("#3987e5", "#d95926", "#199e70", "#c98500",
            "#d55181", "#008300", "#9085e9", "#e66767"),
    sequential=("#0d366b", "#184f95", "#256abf", "#2a78d6",
                "#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb"),
    pos="#3987e5", neg="#e66767", mid="#383835",
    good="#0ca30c", critical="#d03b3b", warning="#fab219",
    selected="#ffffff",
)

THEMES = {"light": LIGHT, "dark": DARK}
