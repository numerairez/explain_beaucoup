"""Synthetic governed dataset for the MVP demo.

Geography / Product / Channel / Customer segment x monthly time, with
Revenue, Volume, Margin and Customers - exactly the shape the design plan's
recommended MVP demo calls for.

The generator deliberately plants a *findable* story so the deterministic
explain engine has real structure to rank:

  * Aug 2026 revenue falls nationally.
  * The fall is concentrated in Cards, on the Digital channel, in Luzon
    (a failed payments migration).
  * Personal Loans in Luzon moves the *other* way - a large offsetting
    contributor, which naive "top mover" ranking would miss.
  * Cebu runs unusually hot against its own history (an exception).
  * Wallet appears in Mindanao only from Jun 2026 (a new contributor).
"""

from __future__ import annotations

import gzip
from pathlib import Path

import numpy as np
import pandas as pd

from ..core.context import month_add

DATA_PATH = Path(__file__).with_name("sales_monthly.csv.gz")

GEOGRAPHY: dict[str, dict[str, list[str]]] = {
    "Luzon": {
        "Metro Manila": ["Quezon City", "Makati", "Taguig"],
        "Cavite": ["Bacoor", "Dasmarinas"],
        "Laguna": ["Santa Rosa", "Calamba"],
        "Pampanga": ["Angeles", "San Fernando"],
    },
    "Visayas": {
        "Cebu": ["Cebu City", "Mandaue"],
        "Iloilo": ["Iloilo City"],
        "Negros Occidental": ["Bacolod"],
    },
    "Mindanao": {
        "Davao del Sur": ["Davao City"],
        "Misamis Oriental": ["Cagayan de Oro"],
        "Zamboanga del Sur": ["Zamboanga City"],
    },
}

PRODUCTS: dict[str, list[str]] = {
    "Payments": ["Cards", "Wallet", "Remittance"],
    "Lending": ["Personal Loans", "Auto Loans", "SME Loans"],
    "Deposits": ["Savings", "Time Deposit"],
    "Protection": ["Life Insurance", "Non-Life Insurance"],
}

CHANNELS = ["Branch", "Digital", "Agent", "Call Center", "Partner"]
SEGMENTS = ["Mass", "Affluent", "SME", "Corporate"]

END_MONTH = "2026-08"
N_MONTHS = 24
START_MONTH = month_add(END_MONTH, -(N_MONTHS - 1))

# Relative weights - give the data a believable shape.
REGION_W = {"Luzon": 1.00, "Visayas": 0.42, "Mindanao": 0.31}
PRODUCT_W = {
    "Cards": 1.00, "Wallet": 0.30, "Remittance": 0.45,
    "Personal Loans": 0.80, "Auto Loans": 0.55, "SME Loans": 0.60,
    "Savings": 0.50, "Time Deposit": 0.35,
    "Life Insurance": 0.28, "Non-Life Insurance": 0.22,
}
CHANNEL_W = {"Branch": 1.00, "Digital": 0.85, "Agent": 0.40,
             "Call Center": 0.25, "Partner": 0.30}
SEGMENT_W = {"Mass": 1.00, "Affluent": 0.55, "SME": 0.45, "Corporate": 0.65}
# Margin rate varies by product - so margin_pct is not a constant.
MARGIN_RATE = {
    "Cards": 0.29, "Wallet": 0.14, "Remittance": 0.33,
    "Personal Loans": 0.41, "Auto Loans": 0.26, "SME Loans": 0.35,
    "Savings": 0.18, "Time Deposit": 0.12,
    "Life Insurance": 0.47, "Non-Life Insurance": 0.38,
}
AVG_TICKET = {
    "Cards": 2_400, "Wallet": 700, "Remittance": 1_900,
    "Personal Loans": 42_000, "Auto Loans": 96_000, "SME Loans": 128_000,
    "Savings": 5_200, "Time Deposit": 31_000,
    "Life Insurance": 18_000, "Non-Life Insurance": 9_400,
}


def _months() -> list[str]:
    return [month_add(START_MONTH, i) for i in range(N_MONTHS)]


def build_frame(seed: int = 20260928) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    months = _months()
    month_index = {m: i for i, m in enumerate(months)}

    cells = []
    for region, provinces in GEOGRAPHY.items():
        for province, cities in provinces.items():
            for city in cities:
                for category, products in PRODUCTS.items():
                    for product in products:
                        for channel in CHANNELS:
                            for segment in SEGMENTS:
                                cells.append((region, province, city, category,
                                              product, channel, segment))

    # Structural sparsity: not every combination exists. Keeps "new member" and
    # "missing member" signals honest rather than synthetic artefacts.
    keep = rng.random(len(cells)) < 0.62
    cells = [c for c, k in zip(cells, keep) if k]
    # Cell-level scale, fixed across time (a store's intrinsic size).
    scale = rng.lognormal(mean=0.0, sigma=0.45, size=len(cells))

    rows: list[dict] = []
    for (region, province, city, category, product, channel, segment), sc in zip(cells, scale):
        base = (180_000 * REGION_W[region] * PRODUCT_W[product]
                * CHANNEL_W[channel] * SEGMENT_W[segment] * sc)
        # Per-cell trend and seasonality so trends are not all identical.
        drift = rng.normal(0.004, 0.006)
        seas_amp = rng.uniform(0.04, 0.13)
        seas_phase = rng.uniform(0, 2 * np.pi)
        noise = rng.normal(0, 0.055, size=len(months))

        # Wallet in Mindanao is a new contributor from Jun 2026.
        first_i = 0
        if product == "Wallet" and region == "Mindanao":
            first_i = month_index["2026-06"]

        for i, month in enumerate(months):
            if i < first_i:
                continue
            f = (1 + drift) ** i
            f *= 1 + seas_amp * np.sin(2 * np.pi * (i % 12) / 12 + seas_phase)
            f *= 1 + noise[i]

            # ---- planted story -------------------------------------------
            if month == END_MONTH:
                if product == "Cards" and region == "Luzon" and channel == "Digital":
                    f *= 0.46          # failed payments migration
                elif product == "Cards" and region == "Luzon":
                    f *= 0.88          # partial spillover
                elif product == "Personal Loans" and region == "Luzon":
                    f *= 1.19          # large offsetting contributor
            if province == "Cebu" and i >= month_index["2026-07"]:
                f *= 1.28              # exception vs its own history

            revenue = max(base * f, 0.0)
            ticket = AVG_TICKET[product] * rng.uniform(0.9, 1.1)
            volume = max(int(round(revenue / ticket)), 0)
            margin = revenue * MARGIN_RATE[product] * rng.uniform(0.9, 1.1)
            customers = max(int(round(volume * rng.uniform(0.25, 0.6))), 0)

            rows.append({
                "month": month,
                "country": "Philippines",
                "region": region,
                "province": province,
                "city": city,
                "product_category": category,
                "product": product,
                "channel": channel,
                "customer_segment": segment,
                "revenue": round(revenue, 2),
                "volume": volume,
                "margin": round(margin, 2),
                "customers": customers,
            })

    df = pd.DataFrame(rows)
    return df.sort_values(["month", "region", "product"]).reset_index(drop=True)


def load_frame(*, rebuild: bool = False) -> pd.DataFrame:
    """Load the cached dataset, generating it deterministically on first run."""
    if DATA_PATH.exists() and not rebuild:
        with gzip.open(DATA_PATH, "rt") as fh:
            return pd.read_csv(fh, dtype={"month": "string"})
    df = build_frame()
    with gzip.open(DATA_PATH, "wt", newline="") as fh:
        df.to_csv(fh, index=False)
    return df


if __name__ == "__main__":
    frame = load_frame(rebuild=True)
    print(frame.shape)
    print(frame.head())
    tot = frame.groupby("month")["revenue"].sum()
    print(tot.tail(4).apply(lambda v: f"{v:,.0f}"))
    print("MoM Aug:", f"{(tot.iloc[-1] / tot.iloc[-2] - 1) * 100:.1f}%")
