"""Kimball Star Schema builder and exporter for Microsoft Power BI (PL-300) analytics."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from src.analysis import build_wide_price_matrix, identify_market_drop_events
from src.config import BRAND_COLOURS, BRAND_TIERS, PipelineConfig, load_parquet, save_parquet

logger = logging.getLogger(__name__)


def build_dim_date(dates: pd.Series) -> pd.DataFrame:
    """Build a contiguous Kimball Date Dimension table spanning the dataset date range."""
    min_date = pd.to_datetime(dates.min()).normalize()
    max_date = pd.to_datetime(dates.max()).normalize()
    calendar = pd.date_range(start=min_date, end=max_date, freq="D")

    dim_date = pd.DataFrame({"date": calendar})
    dim_date["date_key"] = dim_date["date"].dt.strftime("%Y%m%d").astype(int)
    dim_date["year"] = dim_date["date"].dt.year
    dim_date["quarter"] = dim_date["date"].dt.quarter
    dim_date["year_quarter"] = dim_date["year"].astype(str) + "-Q" + dim_date["quarter"].astype(str)
    dim_date["month"] = dim_date["date"].dt.month
    dim_date["month_name"] = dim_date["date"].dt.strftime("%B")
    dim_date["month_short"] = dim_date["date"].dt.strftime("%b")
    dim_date["year_month"] = dim_date["date"].dt.strftime("%Y-%m")
    dim_date["week_of_year"] = dim_date["date"].dt.isocalendar().week.astype(int)
    dim_date["day_of_week"] = dim_date["date"].dt.dayofweek + 1
    dim_date["day_name"] = dim_date["date"].dt.strftime("%A")
    dim_date["is_weekend"] = dim_date["day_of_week"].isin([6, 7])
    dim_date["is_monday"] = dim_date["day_of_week"] == 1

    cols = [
        "date_key",
        "date",
        "year",
        "quarter",
        "year_quarter",
        "month",
        "month_name",
        "month_short",
        "year_month",
        "week_of_year",
        "day_of_week",
        "day_name",
        "is_weekend",
        "is_monday",
    ]
    return dim_date[cols]


def build_dim_brand(
    brands: List[str],
    config: Optional[PipelineConfig] = None,
) -> pd.DataFrame:
    """Build the Kimball Brand Dimension table with surrogate keys, market tiers, and brand colours."""
    cfg = config or PipelineConfig()
    unique_brands = sorted(set(brands))

    rows: List[Dict[str, object]] = []
    for idx, brand_name in enumerate(unique_brands, start=1):
        rows.append(
            {
                "brand_key": idx,
                "brand_name": brand_name,
                "brand_tier": BRAND_TIERS.get(brand_name, "Other"),
                "brand_hex_colour": cfg.brand_colours.get(brand_name, BRAND_COLOURS.get(brand_name, "#888888")),
                "is_focus_brand": brand_name == cfg.focus_brand,
            }
        )
    return pd.DataFrame(rows)


def build_dim_location(station_df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Build the Kimball Station Location Dimension table from cleaned station-level records."""
    if station_df is None or station_df.empty:
        return pd.DataFrame(
            columns=["location_key", "station_name", "address", "suburb", "region", "brand_name"]
        )

    loc_cols = [c for c in ["station_name", "address", "suburb", "region", "brand_clean"] if c in station_df.columns]
    dedup = (
        station_df[loc_cols]
        .drop_duplicates(subset=["station_name", "brand_clean"] if "station_name" in loc_cols else loc_cols)
        .sort_values(loc_cols)
        .reset_index(drop=True)
    )
    dedup = dedup.rename(columns={"brand_clean": "brand_name"})
    dedup.insert(0, "location_key", range(1, len(dedup) + 1))
    return dedup


def build_fact_daily_brand_pricing(
    daily_avg_df: pd.DataFrame,
    dim_brand: pd.DataFrame,
    config: Optional[PipelineConfig] = None,
) -> pd.DataFrame:
    """Build the primary daily brand pricing Fact table with pre-computed baselines, spreads, and flags."""
    cfg = config or PipelineConfig()
    df = daily_avg_df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.sort_values(["brand_clean", "date"]).reset_index(drop=True)

    brand_key_map = dict(zip(dim_brand["brand_name"], dim_brand["brand_key"]))
    df["date_key"] = df["date"].dt.strftime("%Y%m%d").astype(int)
    df["brand_key"] = df["brand_clean"].map(brand_key_map).astype(int)

    df["rolling_7d_price_cpl"] = (
        df.groupby("brand_clean")["avg_price"]
        .transform(lambda s: s.rolling(window=cfg.rolling_window_days, min_periods=cfg.rolling_min_periods).mean())
        .round(2)
    )
    baseline_days = cfg.weekly_lookback_weeks * 7
    df["rolling_56d_baseline_cpl"] = (
        df.groupby("brand_clean")["avg_price"]
        .transform(lambda s: s.rolling(window=baseline_days, min_periods=7).mean())
        .round(2)
    )

    wide, _ = build_wide_price_matrix(df, cfg)
    daily_market_avg = wide.mean(axis=1).round(2).rename("daily_market_avg_cpl")

    competitors = [c for c in wide.columns if c != cfg.focus_brand]
    if competitors:
        daily_cheapest_comp = wide[competitors].min(axis=1).round(2).rename("daily_cheapest_competitor_cpl")
    else:
        daily_cheapest_comp = wide.min(axis=1).round(2).rename("daily_cheapest_competitor_cpl")

    drop_events, market_change = identify_market_drop_events(wide, cfg)
    drop_dates_set = set(pd.to_datetime(drop_events).normalize())
    daily_mkt_change = market_change.round(2).rename("daily_market_change_cpl")

    df = df.merge(daily_market_avg, left_on="date", right_index=True, how="left")
    df = df.merge(daily_cheapest_comp, left_on="date", right_index=True, how="left")
    df = df.merge(daily_mkt_change, left_on="date", right_index=True, how="left")

    df["spread_vs_market_cpl"] = (df["avg_price"] - df["daily_market_avg_cpl"]).round(2)
    df["spread_vs_cheapest_competitor_cpl"] = (df["avg_price"] - df["daily_cheapest_competitor_cpl"]).round(2)
    df["daily_price_rank"] = (
        df.groupby("date")["avg_price"].rank(method="min", na_option="bottom").astype(int)
    )

    df["deviation_from_8wk_baseline_cpl"] = (df["avg_price"] - df["rolling_56d_baseline_cpl"]).round(2)
    df["is_anomaly_2cpl"] = df["deviation_from_8wk_baseline_cpl"].abs() > cfg.weekly_anomaly_threshold_cpl
    df["is_spread_over_5cpl"] = df["spread_vs_cheapest_competitor_cpl"] > cfg.high_spread_threshold_cpl
    df["is_at_or_below_cheapest_competitor"] = df["spread_vs_cheapest_competitor_cpl"] <= 0.0
    df["is_market_drop_event_day"] = df["date"].isin(drop_dates_set)

    df = df.rename(
        columns={
            "avg_price": "avg_price_cpl",
            "median_price": "median_price_cpl",
            "min_price": "min_price_cpl",
            "max_price": "max_price_cpl",
        }
    )

    ordered_cols = [
        "date_key",
        "brand_key",
        "date",
        "brand_clean",
        "avg_price_cpl",
        "median_price_cpl",
        "min_price_cpl",
        "max_price_cpl",
        "station_count",
        "rolling_7d_price_cpl",
        "rolling_56d_baseline_cpl",
        "daily_market_avg_cpl",
        "daily_market_change_cpl",
        "daily_cheapest_competitor_cpl",
        "spread_vs_market_cpl",
        "spread_vs_cheapest_competitor_cpl",
        "daily_price_rank",
        "deviation_from_8wk_baseline_cpl",
        "is_anomaly_2cpl",
        "is_spread_over_5cpl",
        "is_at_or_below_cheapest_competitor",
        "is_market_drop_event_day",
    ]
    return df[ordered_cols].sort_values(["date_key", "brand_key"]).reset_index(drop=True)


def build_fact_market_drop_response(
    daily_avg_df: pd.DataFrame,
    dim_brand: pd.DataFrame,
    config: Optional[PipelineConfig] = None,
) -> pd.DataFrame:
    """Build the Market Drop Event Response Fact table capturing follower lag and 7-day trajectories."""
    cfg = config or PipelineConfig()
    wide, _ = build_wide_price_matrix(daily_avg_df, cfg)
    drop_events, market_change = identify_market_drop_events(wide, cfg)
    brand_key_map = dict(zip(dim_brand["brand_name"], dim_brand["brand_key"]))

    records: List[Dict[str, object]] = []
    event_counter = 0

    for event_date in drop_events:
        event_idx = wide.index.get_loc(event_date)
        if not isinstance(event_idx, int):
            continue
        if event_idx < 1 or event_idx + cfg.response_lag_window_days >= len(wide):
            continue

        event_counter += 1
        pre_price = wide.iloc[event_idx - 1]
        event_date_ts = pd.to_datetime(event_date).normalize()
        event_date_key = int(event_date_ts.strftime("%Y%m%d"))
        mkt_drop_val = round(float(market_change.loc[event_date]), 2)

        for brand in wide.columns:
            if pd.isna(pre_price[brand]) or brand not in brand_key_map:
                continue

            target = pre_price[brand] - cfg.follower_drop_target_cpl
            window_prices = wide.iloc[event_idx : event_idx + cfg.response_lag_window_days][brand]
            days_below = window_prices[window_prices <= target]
            lag_days = int((days_below.index[0] - event_date).days) if len(days_below) > 0 else np.nan

            row: Dict[str, object] = {
                "event_id": event_counter,
                "event_date_key": event_date_key,
                "event_date": event_date_ts,
                "brand_key": brand_key_map[brand],
                "brand_clean": brand,
                "market_drop_cpl": mkt_drop_val,
                "pre_event_price_cpl": round(float(pre_price[brand]), 2),
                "lag_days": lag_days,
                "followed_within_14d": not pd.isna(lag_days),
            }

            for d in range(0, cfg.response_curve_days + 1):
                if event_idx + d < len(wide):
                    post_val = wide.iloc[event_idx + d].get(brand)
                    row[f"day_{d}_change_cpl"] = (
                        round(float(post_val - pre_price[brand]), 2) if not pd.isna(post_val) else np.nan
                    )
                else:
                    row[f"day_{d}_change_cpl"] = np.nan

            records.append(row)

    return pd.DataFrame(records)


def build_star_schema_tables(
    daily_avg_df: pd.DataFrame,
    station_df: Optional[pd.DataFrame] = None,
    config: Optional[PipelineConfig] = None,
) -> Dict[str, pd.DataFrame]:
    """Assemble all Kimball Star Schema dimension and fact tables in memory."""
    cfg = config or PipelineConfig()
    dim_date = build_dim_date(daily_avg_df["date"])
    dim_brand = build_dim_brand(daily_avg_df["brand_clean"].unique().tolist(), cfg)
    dim_location = build_dim_location(station_df)
    fact_daily_brand_pricing = build_fact_daily_brand_pricing(daily_avg_df, dim_brand, cfg)
    fact_market_drop_response = build_fact_market_drop_response(daily_avg_df, dim_brand, cfg)

    return {
        "dim_date": dim_date,
        "dim_brand": dim_brand,
        "dim_location": dim_location,
        "fact_daily_brand_pricing": fact_daily_brand_pricing,
        "fact_market_drop_response": fact_market_drop_response,
    }


def export_star_schema(
    daily_avg_df: Optional[pd.DataFrame] = None,
    station_df: Optional[pd.DataFrame] = None,
    output_dir: Optional[Path] = None,
    config: Optional[PipelineConfig] = None,
) -> Dict[str, pd.DataFrame]:
    """Build and persist Kimball Star Schema tables as both Parquet and CSV for Power BI ingestion."""
    cfg = config or PipelineConfig()
    target_dir = Path(output_dir) if output_dir is not None else cfg.star_schema_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    daily_df = daily_avg_df if daily_avg_df is not None else load_parquet(cfg.daily_averages_parquet_path)
    st_df = station_df
    if st_df is None and cfg.station_parquet_path.exists():
        st_df = load_parquet(cfg.station_parquet_path)

    tables = build_star_schema_tables(daily_df, st_df, cfg)
    for table_name, df in tables.items():
        if df.empty and table_name == "dim_location":
            continue
        parquet_path = target_dir / f"{table_name}.parquet"
        csv_path = target_dir / f"{table_name}.csv"
        save_parquet(df, parquet_path)
        df.to_csv(csv_path, index=False)
        logger.info("Exported Star Schema table '%s' (%d rows) to %s", table_name, len(df), target_dir)

    return tables
