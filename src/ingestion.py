"""Data acquisition, schema standardisation, outlier filtering, and Parquet persistence."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.config import PipelineConfig, save_parquet

logger = logging.getLogger(__name__)


def normalise_brand(brand_name: object) -> str:
    """Normalise raw FuelWatch brand descriptions into canonical retail brand labels."""
    if pd.isna(brand_name):
        return "Unknown"
    b = str(brand_name).strip()
    lower = b.lower()

    if "7-eleven" in lower or "7eleven" in lower:
        return "7-Eleven"
    if "ampol" in lower:
        return "Ampol"
    if b.upper() == "BP" or b.upper().startswith("BP "):
        return "BP"
    if "coles" in lower:
        return "Coles Express"
    if "shell" in lower:
        return "Shell"
    if "united" in lower:
        return "United"
    if "puma" in lower:
        return "Puma"
    if "woolworths" in lower:
        return "Woolworths"
    if "metro" in lower:
        return "Metro"
    return b


def standardise_columns(
    raw_df: pd.DataFrame,
    column_map: Optional[Dict[str, str]] = None,
) -> pd.DataFrame:
    """Normalise raw FuelWatch CSV headers across multi-year format variations."""
    cfg_map = column_map or PipelineConfig().column_map
    df = raw_df.copy()
    df.columns = df.columns.astype(str).str.strip().str.lower().str.replace(" ", "_", regex=False)

    cols_present = {k: v for k, v in cfg_map.items() if k in df.columns}
    missing_required = {"publish_date", "brand_description", "product_description", "product_price"} - set(
        cols_present.keys()
    )
    if missing_required and not {"date", "brand", "product_name", "price_cpl"}.issubset(set(df.columns)):
        raise ValueError(f"Raw FuelWatch data is missing required columns: {sorted(missing_required)}")

    retained_cols = list(cols_present.keys())
    if "source_file" in df.columns and "source_file" not in retained_cols:
        retained_cols.append("source_file")

    if cols_present:
        df = df[retained_cols].rename(columns=cols_present)

    return df


def load_raw_csv_files(raw_dir: Path) -> pd.DataFrame:
    """Load and concatenate all monthly FuelWatch CSV files from the specified directory."""
    csv_files = sorted(Path(raw_dir).glob("*.csv"))
    if not csv_files:
        raise FileNotFoundError(
            f"No CSV files found in '{raw_dir}'. Place monthly FuelWatch CSV files in '{raw_dir}' "
            "or run with '--demo' to execute on synthetic sample data."
        )

    logger.info("Loading %d raw FuelWatch CSV files from %s", len(csv_files), raw_dir)
    frames: List[pd.DataFrame] = []
    for filepath in csv_files:
        frame = pd.read_csv(filepath, low_memory=False)
        frame["source_file"] = filepath.name
        frames.append(frame)

    combined = pd.concat(frames, ignore_index=True)
    logger.info("Loaded %s total raw station-day rows", f"{len(combined):,}")
    return combined


def clean_station_dataframe(
    raw_df: pd.DataFrame,
    config: Optional[PipelineConfig] = None,
) -> pd.DataFrame:
    """Clean station-level records: standardise columns, filter ULP, remove price outliers, normalise brands."""
    cfg = config or PipelineConfig()
    df = standardise_columns(raw_df, cfg.column_map)

    df["date"] = pd.to_datetime(df["date"], dayfirst=True, errors="coerce")
    df["price_cpl"] = pd.to_numeric(df["price_cpl"], errors="coerce")
    df = df.dropna(subset=["date", "price_cpl", "brand", "product_name"]).copy()

    ulp_pattern = "|".join(cfg.ulp_keywords)
    ulp_mask = df["product_name"].astype(str).str.lower().str.contains(ulp_pattern, na=False)
    df_ulp = df[ulp_mask].copy()
    logger.info(
        "Filtered to Unleaded Petrol: %s rows retained (%s dropped)",
        f"{len(df_ulp):,}",
        f"{len(df) - len(df_ulp):,}",
    )

    valid_price_mask = (df_ulp["price_cpl"] >= cfg.min_valid_price_cpl) & (
        df_ulp["price_cpl"] <= cfg.max_valid_price_cpl
    )
    outliers_removed = int((~valid_price_mask).sum())
    df_clean = df_ulp[valid_price_mask].copy()
    logger.info(
        "Outlier check ([%.1f, %.1f] cpl): %d rows removed, %s rows remaining",
        cfg.min_valid_price_cpl,
        cfg.max_valid_price_cpl,
        outliers_removed,
        f"{len(df_clean):,}",
    )

    brand_pattern = "|".join(cfg.brands_of_interest)
    df_filtered = df_clean[df_clean["brand"].astype(str).str.contains(brand_pattern, case=False, na=False)].copy()
    df_filtered["brand_clean"] = df_filtered["brand"].apply(normalise_brand)
    df_filtered = df_filtered[df_filtered["brand_clean"].isin(cfg.brands_of_interest)].copy()

    final_cols = ["date", "brand_clean", "price_cpl", "suburb", "region", "station_name", "address"]
    final_cols = [c for c in final_cols if c in df_filtered.columns]

    df_final = df_filtered[final_cols].sort_values(["date", "brand_clean"]).reset_index(drop=True)
    logger.info("Final cleaned station-level dataset shape: %s", df_final.shape)
    return df_final


def validate_cleaned_dataset(df_final: pd.DataFrame) -> pd.DataFrame:
    """Run data quality and brand date-coverage checks on the cleaned station-level dataset."""
    if df_final.empty:
        raise ValueError("Cleaned dataset is empty after filtering.")

    key_cols = [c for c in ["date", "brand_clean", "price_cpl", "suburb", "region"] if c in df_final.columns]
    null_counts = df_final[key_cols].isnull().sum()
    if (null_counts > 0).any():
        raise ValueError(f"Null values detected in key columns: {null_counts[null_counts > 0].to_dict()}")

    coverage = (
        df_final.groupby("brand_clean")["date"]
        .agg(["min", "max", "nunique"])
        .rename(columns={"min": "first_date", "max": "last_date", "nunique": "days_with_data"})
    )
    total_days = max(int((df_final["date"].max() - df_final["date"].min()).days) + 1, 1)
    coverage["coverage_pct"] = (coverage["days_with_data"] / total_days * 100.0).round(1)
    return coverage


def aggregate_daily_brand_averages(df_final: pd.DataFrame) -> pd.DataFrame:
    """Aggregate station-level prices into daily brand-level summary metrics."""
    agg_spec: Dict[str, Tuple[str, str]] = {
        "avg_price": ("price_cpl", "mean"),
        "median_price": ("price_cpl", "median"),
    }
    if "station_name" in df_final.columns:
        agg_spec["station_count"] = ("station_name", "nunique")
    else:
        agg_spec["station_count"] = ("price_cpl", "count")
    agg_spec["min_price"] = ("price_cpl", "min")
    agg_spec["max_price"] = ("price_cpl", "max")

    daily_avg = (
        df_final.groupby(["date", "brand_clean"], as_index=False)
        .agg(**agg_spec)
        .sort_values(["date", "brand_clean"])
        .reset_index(drop=True)
    )
    num_cols = ["avg_price", "median_price", "min_price", "max_price"]
    daily_avg[num_cols] = daily_avg[num_cols].round(2)
    return daily_avg


def run_ingestion_pipeline(
    config: Optional[PipelineConfig] = None,
    raw_df: Optional[pd.DataFrame] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Execute the full Notebook 1 data acquisition, cleaning, validation, and Parquet export workflow."""
    cfg = config or PipelineConfig()
    cfg.processed_dir.mkdir(parents=True, exist_ok=True)

    source_df = raw_df if raw_df is not None else load_raw_csv_files(cfg.raw_dir)
    df_final = clean_station_dataframe(source_df, cfg)
    coverage = validate_cleaned_dataset(df_final)
    daily_avg = aggregate_daily_brand_averages(df_final)

    save_parquet(df_final, cfg.station_parquet_path)
    save_parquet(daily_avg, cfg.daily_averages_parquet_path)
    daily_avg.to_csv(cfg.daily_averages_csv_path, index=False)

    logger.info("Saved station-level Parquet to %s", cfg.station_parquet_path)
    logger.info("Saved daily brand averages Parquet to %s", cfg.daily_averages_parquet_path)
    logger.info("Saved daily brand averages CSV to %s", cfg.daily_averages_csv_path)
    return df_final, daily_avg, coverage


def generate_synthetic_fuelwatch_data(
    start_date: str = "2024-01-01",
    days: int = 180,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate realistic synthetic WA FuelWatch station-level records for testing and demo runs."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range(start=start_date, periods=days, freq="D")

    brand_specs = [
        ("7-Eleven", 0.0, 5, "Metro", "South of River"),
        ("Ampol", 2.8, 6, "Metro", "North of River"),
        ("BP", 3.4, 6, "Metro", "South of River"),
        ("Coles Express", 4.5, 4, "Metro", "North of River"),
        ("Shell", -0.8, 4, "Metro", "South of River"),
        ("United", -3.5, 4, "Peel", "Murray"),
        ("Puma", -0.5, 4, "Metro", "North of River"),
        ("Woolworths", 0.2, 3, "Metro", "South of River"),
        ("Metro Petroleum", -5.2, 3, "Metro", "North of River"),
    ]

    records: List[Dict[str, object]] = []
    for day_idx, current_date in enumerate(dates):
        cycle_pos = day_idx % 14
        if cycle_pos == 0:
            cycle_effect = -8.0
        elif cycle_pos <= 3:
            cycle_effect = -12.0 + cycle_pos * 1.5
        else:
            cycle_effect = -6.0 + (cycle_pos - 3) * 1.6

        macro_wave = 6.0 * np.sin(2.0 * np.pi * day_idx / 60.0)
        base_market_price = 188.0 + cycle_effect + macro_wave

        for brand_label, brand_offset, n_stations, region, area in brand_specs:
            lag_adjustment = 0.0
            if cycle_pos in (0, 1) and brand_label in ("Metro Petroleum", "Shell", "Coles Express"):
                lag_adjustment = 4.2
            elif cycle_pos in (1, 2, 3) and brand_label == "7-Eleven":
                lag_adjustment = -2.5

            for st_idx in range(1, n_stations + 1):
                station_noise = float(rng.normal(0.0, 1.1))
                price = round(base_market_price + brand_offset + lag_adjustment + station_noise, 1)
                records.append(
                    {
                        "PUBLISH_DATE": current_date.strftime("%d/%m/%Y"),
                        "TRADING_NAME": f"{brand_label} Station {st_idx}",
                        "BRAND_DESCRIPTION": brand_label,
                        "PRODUCT_DESCRIPTION": "ULP",
                        "PRODUCT_PRICE": price,
                        "ADDRESS": f"{100 + st_idx} Albany Hwy",
                        "LOCATION": f"SUBURB_{st_idx}",
                        "AREA_DESCRIPTION": area,
                        "REGION_DESCRIPTION": region,
                        "source_file": f"FuelWatchRetail-{current_date.strftime('%m-%Y')}.csv",
                    }
                )

    return pd.DataFrame(records)
