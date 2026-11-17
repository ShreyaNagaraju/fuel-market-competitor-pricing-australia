"""Competitor pricing analysis: market positioning, daily price spread, and market-drop response lag."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

from src.config import BRAND_COLOURS, MATPLOTLIB_STYLE_RC, PipelineConfig, load_parquet

logger = logging.getLogger(__name__)


def apply_chart_style() -> None:
    """Apply consistent publication-grade Matplotlib styling across all pipeline charts."""
    plt.rcParams.update(MATPLOTLIB_STYLE_RC)


def build_wide_price_matrix(
    daily_avg_df: pd.DataFrame,
    config: Optional[PipelineConfig] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Pivot daily brand averages into a wide date-by-brand matrix and its 7-day rolling mean."""
    cfg = config or PipelineConfig()
    df = daily_avg_df.copy()
    df["date"] = pd.to_datetime(df["date"])

    wide = df.pivot_table(index="date", columns="brand_clean", values="avg_price").sort_index()
    wide_smooth = wide.rolling(window=cfg.rolling_window_days, min_periods=cfg.rolling_min_periods).mean()
    return wide, wide_smooth


def compute_brand_positioning_summary(daily_avg_df: pd.DataFrame) -> pd.DataFrame:
    """Compute brand-level summary statistics and average market rank (1 = cheapest)."""
    summary = (
        daily_avg_df.groupby("brand_clean")
        .agg(
            avg_price=("avg_price", "mean"),
            median_price=("avg_price", "median"),
            min_price=("avg_price", "min"),
            max_price=("avg_price", "max"),
            price_range=("avg_price", lambda x: x.max() - x.min()),
        )
        .round(1)
        .sort_values("avg_price")
    )
    summary["avg_rank"] = range(1, len(summary) + 1)
    return summary


def plot_market_positioning(
    wide_smooth: pd.DataFrame,
    save_path: Optional[Path] = None,
    config: Optional[PipelineConfig] = None,
) -> plt.Figure:
    """Render Chart 01: 7-day rolling average daily unleaded petrol price by brand."""
    cfg = config or PipelineConfig()
    apply_chart_style()

    fig, ax = plt.subplots(figsize=(14, 6))
    for brand in wide_smooth.columns.tolist():
        colour = cfg.brand_colours.get(brand, BRAND_COLOURS.get(brand, "#AAAAAA"))
        lw = 2.5 if brand == cfg.focus_brand else 1.2
        alpha = 1.0 if brand == cfg.focus_brand else 0.55
        zorder = 5 if brand == cfg.focus_brand else 2
        ax.plot(
            wide_smooth.index,
            wide_smooth[brand],
            label=brand,
            color=colour,
            linewidth=lw,
            alpha=alpha,
            zorder=zorder,
        )

    ax.set_title(
        "Retail Unleaded Petrol: Daily Average Price by Brand (7-day rolling average)",
        fontweight="bold",
        pad=14,
    )
    ax.set_ylabel("Price (cents per litre)")
    ax.set_xlabel("")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    plt.xticks(rotation=30, ha="right")
    ax.legend(loc="upper left", ncol=2)
    ax.set_xlim(wide_smooth.index.min(), wide_smooth.index.max())

    plt.tight_layout()
    if save_path:
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(save_path, dpi=cfg.chart_dpi, bbox_inches="tight")
        logger.info("Saved market positioning chart to %s", save_path)
    plt.close(fig)
    return fig


def compute_daily_price_spread(
    wide: pd.DataFrame,
    config: Optional[PipelineConfig] = None,
) -> Tuple[pd.Series, pd.Series, Dict[str, float]]:
    """Compute daily price spread between the focus brand and the cheapest major competitor."""
    cfg = config or PipelineConfig()
    if cfg.focus_brand not in wide.columns:
        raise ValueError(f"Focus brand '{cfg.focus_brand}' not found in wide price matrix.")

    major_competitors = [
        c for c in wide.columns if c != cfg.focus_brand and c in cfg.major_benchmark_brands
    ]
    all_competitors = [c for c in wide.columns if c != cfg.focus_brand]
    if not major_competitors:
        major_competitors = all_competitors

    daily_min_major = wide[major_competitors].min(axis=1)
    daily_min_all = wide[all_competitors].min(axis=1)

    spread = (wide[cfg.focus_brand] - daily_min_major).dropna()
    spread_all = (wide[cfg.focus_brand] - daily_min_all).dropna()
    spread_smooth = spread.rolling(window=cfg.rolling_window_days, min_periods=cfg.rolling_min_periods).mean()

    stats = {
        "mean_spread_cpl": round(float(spread.mean()), 2),
        "median_spread_cpl": round(float(spread.median()), 2),
        "min_spread_cpl": round(float(spread.min()), 2),
        "max_spread_cpl": round(float(spread.max()), 2),
        "days_at_or_below_cheapest": int((spread <= 0).sum()),
        "pct_days_at_or_below_cheapest": round(float((spread <= 0).mean() * 100.0), 1),
        "days_above_threshold": int((spread > cfg.high_spread_threshold_cpl).sum()),
        "pct_days_above_threshold": round(float((spread > cfg.high_spread_threshold_cpl).mean() * 100.0), 1),
        "all_brands_mean_spread_cpl": round(float(spread_all.mean()), 2),
        "all_brands_pct_days_above_threshold": round(
            float((spread_all > cfg.high_spread_threshold_cpl).mean() * 100.0), 1
        ),
    }
    return spread, spread_smooth, stats


def plot_price_spread(
    wide_smooth: pd.DataFrame,
    spread_smooth: pd.Series,
    save_path: Optional[Path] = None,
    config: Optional[PipelineConfig] = None,
) -> plt.Figure:
    """Render Chart 02: two-panel absolute price comparison and daily spread time series."""
    cfg = config or PipelineConfig()
    apply_chart_style()

    competitors = [c for c in wide_smooth.columns if c != cfg.focus_brand]
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 9), gridspec_kw={"height_ratios": [2, 1]})

    ax1.plot(
        wide_smooth.index,
        wide_smooth.get(cfg.focus_brand),
        color="#007041",
        linewidth=2.2,
        label=cfg.focus_brand,
        zorder=4,
    )
    ax1.plot(
        wide_smooth.index,
        wide_smooth[competitors].min(axis=1).rolling(cfg.rolling_window_days, min_periods=cfg.rolling_min_periods).mean(),
        color="#AAAAAA",
        linewidth=1.4,
        linestyle="--",
        label="Cheapest competitor (daily min)",
        zorder=3,
    )
    ax1.set_title(
        f"{cfg.focus_brand} vs Cheapest Competitor: Absolute Price (7-day rolling average)",
        fontweight="bold",
        pad=12,
    )
    ax1.set_ylabel("Price (cents per litre)")
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    ax1.tick_params(labelbottom=False)
    ax1.legend()

    ax2.axhline(0, color="#333333", linewidth=0.8, linestyle="-")
    ax2.fill_between(
        spread_smooth.index,
        spread_smooth,
        0,
        where=(spread_smooth >= 0),
        color="#E63329",
        alpha=0.35,
        label=f"{cfg.focus_brand} above cheapest",
    )
    ax2.fill_between(
        spread_smooth.index,
        spread_smooth,
        0,
        where=(spread_smooth < 0),
        color="#007041",
        alpha=0.35,
        label=f"{cfg.focus_brand} below cheapest",
    )
    ax2.plot(spread_smooth.index, spread_smooth, color="#333333", linewidth=1.2)
    ax2.set_title(
        f"Price Spread: {cfg.focus_brand} minus Cheapest Competitor (cpl)",
        fontweight="bold",
        pad=8,
    )
    ax2.set_ylabel("Spread (cpl)")
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    plt.xticks(rotation=30, ha="right")
    ax2.legend(loc="upper left")
    ax2.set_xlim(spread_smooth.index.min(), spread_smooth.index.max())

    plt.tight_layout()
    if save_path:
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(save_path, dpi=cfg.chart_dpi, bbox_inches="tight")
        logger.info("Saved price spread chart to %s", save_path)
    plt.close(fig)
    return fig


def plot_spread_distribution(
    spread: pd.Series,
    save_path: Optional[Path] = None,
    config: Optional[PipelineConfig] = None,
) -> plt.Figure:
    """Render Chart 03: histogram distribution of daily price spread vs cheapest competitor."""
    cfg = config or PipelineConfig()
    apply_chart_style()

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.hist(spread.dropna(), bins=60, color="#007041", alpha=0.75, edgecolor="white", linewidth=0.4)
    ax.axvline(
        spread.mean(),
        color="#E63329",
        linewidth=1.8,
        linestyle="--",
        label=f"Mean spread: {spread.mean():+.1f} cpl",
    )
    ax.axvline(
        0,
        color="#333333",
        linewidth=1.2,
        linestyle="-",
        label="At parity with cheapest competitor",
    )

    ax.set_title(
        f"Distribution of {cfg.focus_brand} Price Spread vs Cheapest Competitor",
        fontweight="bold",
        pad=12,
    )
    ax.set_xlabel("Spread (cpl): negative means cheaper than all competitors")
    ax.set_ylabel("Number of days")
    ax.legend()

    plt.tight_layout()
    if save_path:
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(save_path, dpi=cfg.chart_dpi, bbox_inches="tight")
        logger.info("Saved spread distribution chart to %s", save_path)
    plt.close(fig)
    return fig


def identify_market_drop_events(
    wide: pd.DataFrame,
    config: Optional[PipelineConfig] = None,
) -> Tuple[pd.DatetimeIndex, pd.Series]:
    """Identify discrete market drop episodes (de-clustered with a minimum gap between episodes)."""
    cfg = config or PipelineConfig()
    peer_cols = [c for c in wide.columns if c != cfg.focus_brand and c in cfg.major_benchmark_brands]
    if not peer_cols:
        peer_cols = [c for c in wide.columns if c != cfg.focus_brand] or list(wide.columns)

    market_avg = wide[peer_cols].mean(axis=1)
    market_change = market_avg.diff()
    raw_drop_dates = market_change[market_change <= cfg.market_drop_threshold_cpl].index

    episode_dates: List[pd.Timestamp] = []
    last_date: Optional[pd.Timestamp] = None
    for d in raw_drop_dates:
        ts = pd.Timestamp(d)
        if last_date is None or (ts - last_date).days >= cfg.min_episode_gap_days:
            episode_dates.append(ts)
            last_date = ts

    return pd.DatetimeIndex(episode_dates), market_change


def compute_competitor_response_lag(
    wide: pd.DataFrame,
    config: Optional[PipelineConfig] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Compute brand-level follower response lag (days) across all significant market drop events."""
    cfg = config or PipelineConfig()
    drop_events, _ = identify_market_drop_events(wide, cfg)

    lag_records: List[Dict[str, object]] = []
    for event_date in drop_events:
        event_idx = wide.index.get_loc(event_date)
        if not isinstance(event_idx, int):
            continue
        if event_idx < 1 or event_idx + cfg.response_lag_window_days >= len(wide):
            continue

        pre_price = wide.iloc[event_idx - 1]
        for brand in wide.columns:
            if pd.isna(pre_price[brand]):
                continue
            target = pre_price[brand] - cfg.follower_drop_target_cpl
            window_prices = wide.iloc[event_idx : event_idx + cfg.response_lag_window_days][brand]
            days_below = window_prices[window_prices <= target]
            lag = int((days_below.index[0] - event_date).days) if len(days_below) > 0 else None
            lag_records.append(
                {
                    "event_date": event_date,
                    "brand": brand,
                    "pre_event_price_cpl": round(float(pre_price[brand]), 2),
                    "lag_days": lag,
                    "present": True,
                }
            )

    lag_df = pd.DataFrame(lag_records)
    if lag_df.empty:
        empty_summary = pd.DataFrame(columns=["median_lag", "mean_lag", "events_followed", "follow_rate"])
        empty_summary.index.name = "brand"
        return lag_df, empty_summary

    total_events_per_brand = lag_df.groupby("brand")["present"].count()
    lag_summary = (
        lag_df.dropna(subset=["lag_days"])
        .groupby("brand")
        .agg(
            median_lag=("lag_days", "median"),
            mean_lag=("lag_days", "mean"),
            events_followed=("lag_days", "count"),
        )
        .round(1)
        .sort_values("median_lag")
    )
    lag_summary["follow_rate"] = (lag_summary["events_followed"] / total_events_per_brand).round(2)
    return lag_df, lag_summary


def plot_response_lag(
    lag_summary: pd.DataFrame,
    save_path: Optional[Path] = None,
    config: Optional[PipelineConfig] = None,
) -> plt.Figure:
    """Render Chart 04: horizontal bar chart of median days to follow a market price drop."""
    cfg = config or PipelineConfig()
    apply_chart_style()

    fig, ax = plt.subplots(figsize=(10, 5))
    brands_sorted = lag_summary.index.tolist()
    colours = [cfg.brand_colours.get(b, BRAND_COLOURS.get(b, "#AAAAAA")) for b in brands_sorted]
    bars = ax.barh(
        brands_sorted,
        lag_summary["median_lag"],
        color=colours,
        edgecolor="white",
        linewidth=0.5,
        height=0.6,
    )

    for bar, val in zip(bars, lag_summary["median_lag"]):
        ax.text(
            val + 0.1,
            bar.get_y() + bar.get_height() / 2.0,
            f"{val:.1f}d",
            va="center",
            ha="left",
            fontsize=9,
            color="#333333",
        )

    ax.set_title("Median Days to Follow a Market Price Drop by Brand", fontweight="bold", pad=12)
    ax.set_xlabel("Median lag (days): lower is faster to follow market drops")
    max_lag = float(lag_summary["median_lag"].max()) if not lag_summary.empty else 3.0
    ax.set_xlim(0, max_lag + 2.0)
    ax.invert_yaxis()

    plt.tight_layout()
    if save_path:
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(save_path, dpi=cfg.chart_dpi, bbox_inches="tight")
        logger.info("Saved response lag chart to %s", save_path)
    plt.close(fig)
    return fig


def compute_response_curves(
    wide: pd.DataFrame,
    config: Optional[PipelineConfig] = None,
) -> Tuple[Dict[str, List[List[float]]], pd.DataFrame]:
    """Compute brand-level price trajectories across days 0..7 following each market drop event."""
    cfg = config or PipelineConfig()
    drop_events, _ = identify_market_drop_events(wide, cfg)
    days_after = list(range(0, cfg.response_curve_days + 1))

    response_curves: Dict[str, List[List[float]]] = {brand: [] for brand in wide.columns}
    for event_date in drop_events:
        event_idx = wide.index.get_loc(event_date)
        if not isinstance(event_idx, int):
            continue
        if event_idx < 1 or event_idx + cfg.response_curve_days >= len(wide):
            continue
        pre_price = wide.iloc[event_idx - 1]
        for brand in wide.columns:
            if pd.isna(pre_price[brand]):
                continue
            changes: List[float] = []
            for d in days_after:
                post_price = wide.iloc[event_idx + d].get(brand)
                if not pd.isna(post_price):
                    changes.append(float(post_price - pre_price[brand]))
                else:
                    changes.append(float("nan"))
            response_curves[brand].append(changes)

    avg_curve_rows: Dict[str, np.ndarray] = {}
    for brand, curves in response_curves.items():
        if curves:
            avg_curve_rows[brand] = np.nanmean(np.array(curves, dtype=float), axis=0).round(2)

    avg_curves_df = pd.DataFrame(avg_curve_rows, index=days_after)
    avg_curves_df.index.name = "days_after_drop"
    return response_curves, avg_curves_df


def plot_response_curves(
    avg_curves_df: pd.DataFrame,
    save_path: Optional[Path] = None,
    config: Optional[PipelineConfig] = None,
) -> plt.Figure:
    """Render Chart 05: average price trajectory in the 7 days following a market drop event."""
    cfg = config or PipelineConfig()
    apply_chart_style()

    fig, ax = plt.subplots(figsize=(11, 5))
    days_after = avg_curves_df.index.tolist()

    for brand in avg_curves_df.columns:
        colour = cfg.brand_colours.get(brand, BRAND_COLOURS.get(brand, "#AAAAAA"))
        lw = 2.5 if brand == cfg.focus_brand else 1.2
        alpha = 1.0 if brand == cfg.focus_brand else 0.55
        zorder = 5 if brand == cfg.focus_brand else 2
        ax.plot(
            days_after,
            avg_curves_df[brand],
            label=brand,
            color=colour,
            linewidth=lw,
            alpha=alpha,
            zorder=zorder,
            marker="o",
            markersize=3,
        )

    ax.axhline(0, color="#333333", linewidth=0.8, linestyle="--")
    ax.set_title("Average Price Change in 7 Days Following a Market Drop Event", fontweight="bold", pad=12)
    ax.set_xlabel("Days after market drop event")
    ax.set_ylabel("Average price change (cpl) vs pre-event price")
    ax.legend(ncol=2)
    ax.xaxis.set_major_locator(mticker.MultipleLocator(1))

    plt.tight_layout()
    if save_path:
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(save_path, dpi=cfg.chart_dpi, bbox_inches="tight")
        logger.info("Saved response curves chart to %s", save_path)
    plt.close(fig)
    return fig


def compute_weekly_cycle_profile(
    daily_avg_df: pd.DataFrame,
    config: Optional[PipelineConfig] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Compute day-of-week price cycle profiles, spreads, and Wednesday restoration hike leadership."""
    cfg = config or PipelineConfig()
    df = daily_avg_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    wide, _ = build_wide_price_matrix(df, cfg)

    major_competitors = [
        c for c in wide.columns if c != cfg.focus_brand and c in cfg.major_benchmark_brands
    ]
    if not major_competitors:
        major_competitors = [c for c in wide.columns if c != cfg.focus_brand]

    # Station-weighted daily market average
    weighted_daily = (
        df.assign(weighted_px=df["avg_price"] * df["station_count"])
        .groupby("date", as_index=True)
        .agg(total_px=("weighted_px", "sum"), total_st=("station_count", "sum"))
    )
    weighted_market = weighted_daily["total_px"] / weighted_daily["total_st"].replace(0, np.nan)

    cycle_frame = pd.DataFrame(index=wide.index)
    cycle_frame["day_of_week"] = cycle_frame.index.dayofweek + 1
    cycle_frame["day_name"] = cycle_frame.index.day_name()
    cycle_frame["focus_price_cpl"] = wide.get(cfg.focus_brand)
    cycle_frame["market_weighted_cpl"] = weighted_market
    cycle_frame["cheapest_major_cpl"] = wide[major_competitors].min(axis=1)
    cycle_frame["spread_vs_cheapest_major_cpl"] = (
        cycle_frame["focus_price_cpl"] - cycle_frame["cheapest_major_cpl"]
    )
    cycle_frame["spread_vs_market_weighted_cpl"] = (
        cycle_frame["focus_price_cpl"] - cycle_frame["market_weighted_cpl"]
    )

    dow_summary = (
        cycle_frame.groupby(["day_of_week", "day_name"], as_index=False)
        .agg(
            focus_avg_cpl=("focus_price_cpl", "mean"),
            market_weighted_cpl=("market_weighted_cpl", "mean"),
            cheapest_major_cpl=("cheapest_major_cpl", "mean"),
            spread_vs_cheapest_major_cpl=("spread_vs_cheapest_major_cpl", "mean"),
            spread_vs_market_weighted_cpl=("spread_vs_market_weighted_cpl", "mean"),
        )
        .sort_values("day_of_week")
        .round(2)
    )

    # Restoration hike leadership (weekly cycle jumps where peer market rises >= +4.0 cpl)
    peer_change = wide[major_competitors].mean(axis=1).diff()
    raw_hike_dates = peer_change[peer_change >= 4.0].index
    hike_episodes: List[pd.Timestamp] = []
    last_hike: Optional[pd.Timestamp] = None
    for d in raw_hike_dates:
        ts = pd.Timestamp(d)
        if last_hike is None or (ts - last_hike).days >= cfg.min_episode_gap_days:
            hike_episodes.append(ts)
            last_hike = ts

    daily_diffs = wide.diff()
    hike_rows: List[Dict[str, object]] = []
    for brand in wide.columns:
        same_day_hikes = 0
        next_day_hikes = 0
        total_ep = 0
        avg_hike_size: List[float] = []
        for h_date in hike_episodes:
            idx = wide.index.get_loc(h_date)
            if not isinstance(idx, int) or idx < 1 or idx + 1 >= len(wide):
                continue
            if pd.isna(wide.iloc[idx - 1][brand]):
                continue
            total_ep += 1
            d0_jump = float(daily_diffs.iloc[idx][brand]) if not pd.isna(daily_diffs.iloc[idx][brand]) else 0.0
            d1_jump = (
                float(daily_diffs.iloc[idx + 1][brand])
                if not pd.isna(daily_diffs.iloc[idx + 1][brand])
                else 0.0
            )
            if d0_jump >= 4.0:
                same_day_hikes += 1
                avg_hike_size.append(d0_jump)
            elif d1_jump >= 4.0:
                next_day_hikes += 1
                avg_hike_size.append(d1_jump)

        if total_ep > 0:
            hike_rows.append(
                {
                    "brand": brand,
                    "hike_episodes": total_ep,
                    "same_day_hike_rate": round(same_day_hikes / total_ep, 2),
                    "within_1d_hike_rate": round((same_day_hikes + next_day_hikes) / total_ep, 2),
                    "mean_hike_cpl": round(float(np.mean(avg_hike_size)), 1) if avg_hike_size else 0.0,
                }
            )

    if hike_rows:
        hike_summary = pd.DataFrame(hike_rows).sort_values("same_day_hike_rate", ascending=False)
    else:
        hike_summary = pd.DataFrame(
            columns=["brand", "hike_episodes", "same_day_hike_rate", "within_1d_hike_rate", "mean_hike_cpl"]
        )
    return dow_summary, hike_summary


def plot_weekly_cycle(
    dow_summary: pd.DataFrame,
    save_path: Optional[Path] = None,
    config: Optional[PipelineConfig] = None,
) -> plt.Figure:
    """Render Chart 07: Weekly Day-of-Week Price Cycle Trough & Restoration Spread Profile."""
    cfg = config or PipelineConfig()
    apply_chart_style()

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
    days = dow_summary["day_name"].str.slice(0, 3).tolist()

    ax1.plot(
        days,
        dow_summary["focus_avg_cpl"],
        marker="o",
        linewidth=2.4,
        color="#007041",
        label=cfg.focus_brand,
    )
    ax1.plot(
        days,
        dow_summary["market_weighted_cpl"],
        marker="s",
        linewidth=1.8,
        linestyle="--",
        color="#333333",
        label="Market avg (station-weighted)",
    )
    ax1.plot(
        days,
        dow_summary["cheapest_major_cpl"],
        marker="^",
        linewidth=1.6,
        linestyle=":",
        color="#0057A8",
        label="Cheapest major rival",
    )
    ax1.set_title("Weekly Price Cycle by Day of Week (cpl)", fontweight="bold", pad=12)
    ax1.set_ylabel("Average ULP Price (cents per litre)")
    ax1.legend()

    bar_colours = [
        "#007041" if val <= 0 else ("#E63329" if val > 5.0 else "#FFC72C")
        for val in dow_summary["spread_vs_cheapest_major_cpl"]
    ]
    bars = ax2.bar(days, dow_summary["spread_vs_cheapest_major_cpl"], color=bar_colours, width=0.55)
    ax2.axhline(0, color="#333333", linewidth=0.9)
    ax2.axhline(5.0, color="#E63329", linewidth=1.0, linestyle="--", label="5.0 cpl risk threshold")
    for bar, val in zip(bars, dow_summary["spread_vs_cheapest_major_cpl"]):
        ax2.text(
            bar.get_x() + bar.get_width() / 2.0,
            val + (0.4 if val >= 0 else -1.0),
            f"{val:+.1f}",
            ha="center",
            va="bottom" if val >= 0 else "top",
            fontsize=9,
            fontweight="bold",
        )
    ax2.set_title(
        f"{cfg.focus_brand} Spread vs Cheapest Major Rival by Day of Week",
        fontweight="bold",
        pad=12,
    )
    ax2.set_ylabel("Spread (cpl)")
    ax2.legend()

    plt.tight_layout()
    if save_path:
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(save_path, dpi=cfg.chart_dpi, bbox_inches="tight")
        logger.info("Saved weekly cycle chart to %s", save_path)
    plt.close(fig)
    return fig


def run_competitor_analysis_pipeline(
    daily_avg_df: Optional[pd.DataFrame] = None,
    config: Optional[PipelineConfig] = None,
    save_charts: bool = True,
) -> Dict[str, object]:
    """Execute the complete Notebook 2 competitor pricing analysis workflow and chart suite."""
    cfg = config or PipelineConfig()
    df = daily_avg_df if daily_avg_df is not None else load_parquet(cfg.daily_averages_parquet_path)

    wide, wide_smooth = build_wide_price_matrix(df, cfg)
    positioning_summary = compute_brand_positioning_summary(df)
    spread, spread_smooth, spread_stats = compute_daily_price_spread(wide, cfg)
    lag_df, lag_summary = compute_competitor_response_lag(wide, cfg)
    _, avg_curves_df = compute_response_curves(wide, cfg)
    dow_summary, hike_summary = compute_weekly_cycle_profile(df, cfg)

    if save_charts:
        cfg.outputs_dir.mkdir(parents=True, exist_ok=True)
        plot_market_positioning(wide_smooth, cfg.outputs_dir / "01_market_positioning.png", cfg)
        plot_price_spread(wide_smooth, spread_smooth, cfg.outputs_dir / "02_price_spread.png", cfg)
        plot_spread_distribution(spread, cfg.outputs_dir / "03_spread_distribution.png", cfg)
        plot_response_lag(lag_summary, cfg.outputs_dir / "04_response_lag.png", cfg)
        plot_response_curves(avg_curves_df, cfg.outputs_dir / "05_response_curves.png", cfg)
        plot_weekly_cycle(dow_summary, cfg.outputs_dir / "07_weekly_price_cycle.png", cfg)

    return {
        "wide": wide,
        "wide_smooth": wide_smooth,
        "positioning_summary": positioning_summary,
        "spread": spread,
        "spread_smooth": spread_smooth,
        "spread_stats": spread_stats,
        "lag_df": lag_df,
        "lag_summary": lag_summary,
        "avg_curves_df": avg_curves_df,
        "dow_summary": dow_summary,
        "hike_summary": hike_summary,
    }
