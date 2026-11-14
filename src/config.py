"""Centralised configuration, constants, and styling for the WA FuelWatch pricing pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Union

import pandas as pd


def save_parquet(df: pd.DataFrame, path: Union[str, Path]) -> None:
    """Write a DataFrame to Parquet using pyarrow/fastparquet, with Polars native fallback."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(target, index=False)
    except ImportError:
        import polars as pl

        pl.DataFrame(df.to_dict(orient="list")).write_parquet(target)


def load_parquet(path: Union[str, Path]) -> pd.DataFrame:
    """Read a Parquet file into a pandas DataFrame using pyarrow/fastparquet, with Polars fallback."""
    target = Path(path)
    try:
        return pd.read_parquet(target)
    except ImportError:
        import polars as pl

        pl_df = pl.read_parquet(target)
        return pd.DataFrame(pl_df.to_dict(as_series=False))


DEFAULT_COLUMN_MAP: Dict[str, str] = {
    "publish_date": "date",
    "brand_description": "brand",
    "product_description": "product_name",
    "product_price": "price_cpl",
    "trading_name": "station_name",
    "address": "address",
    "location": "suburb",
    "area_description": "region",
}

DEFAULT_ULP_KEYWORDS: List[str] = ["unleaded", "ulp", "e10"]

DEFAULT_BRANDS_OF_INTEREST: List[str] = [
    "7-Eleven",
    "Ampol",
    "BP",
    "Coles Express",
    "Shell",
    "United",
    "Puma",
    "Woolworths",
    "Metro",
]

BRAND_COLOURS: Dict[str, str] = {
    "7-Eleven": "#007041",
    "Ampol": "#E63329",
    "BP": "#007A33",
    "Coles Express": "#E2001A",
    "Shell": "#FFC72C",
    "United": "#0057A8",
    "Puma": "#1A1A1A",
    "Woolworths": "#00833E",
    "Metro": "#888888",
}

BRAND_TIERS: Dict[str, str] = {
    "Metro": "Value / Independent",
    "United": "Value / Independent",
    "Shell": "Mid-Market",
    "7-Eleven": "Mid-Market",
    "Woolworths": "Mid-Market",
    "Puma": "Mid-Market",
    "Ampol": "Major / Premium",
    "BP": "Major / Premium",
    "Coles Express": "Major / Premium",
}

MATPLOTLIB_STYLE_RC: Dict[str, object] = {
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "axes.grid": True,
    "grid.color": "#EEEEEE",
    "grid.linewidth": 0.6,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "font.family": "sans-serif",
    "axes.titlesize": 13,
    "axes.labelsize": 11,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
    "legend.fontsize": 9,
    "legend.frameon": False,
}


@dataclass(frozen=True)
class PipelineConfig:
    """Immutable configuration parameters for data ingestion, analysis, and reporting."""

    project_root: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent)
    raw_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent / "data" / "raw")
    processed_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent / "data" / "processed")
    star_schema_dir: Path = field(
        default_factory=lambda: Path(__file__).resolve().parent.parent / "data" / "star_schema"
    )
    outputs_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent / "outputs")

    focus_brand: str = "7-Eleven"
    min_valid_price_cpl: float = 70.0
    max_valid_price_cpl: float = 350.0
    rolling_window_days: int = 7
    rolling_min_periods: int = 3

    market_drop_threshold_cpl: float = -3.0
    follower_drop_target_cpl: float = 2.0
    response_lag_window_days: int = 14
    response_curve_days: int = 7

    weekly_lookback_weeks: int = 8
    weekly_anomaly_threshold_cpl: float = 2.0
    high_spread_threshold_cpl: float = 5.0
    chart_dpi: int = 150

    column_map: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_COLUMN_MAP))
    ulp_keywords: List[str] = field(default_factory=lambda: list(DEFAULT_ULP_KEYWORDS))
    brands_of_interest: List[str] = field(default_factory=lambda: list(DEFAULT_BRANDS_OF_INTEREST))
    brand_colours: Dict[str, str] = field(default_factory=lambda: dict(BRAND_COLOURS))

    @property
    def station_parquet_path(self) -> Path:
        return self.processed_dir / "fuelwatch_clean.parquet"

    @property
    def daily_averages_parquet_path(self) -> Path:
        return self.processed_dir / "daily_brand_averages.parquet"

    @property
    def daily_averages_csv_path(self) -> Path:
        return self.processed_dir / "daily_brand_averages.csv"
