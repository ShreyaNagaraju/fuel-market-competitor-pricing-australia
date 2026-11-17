"""Repeatable weekly fuel market reporting pipeline, anomaly detection, and longitudinal trend tracking."""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

from src.analysis import apply_chart_style
from src.config import BRAND_COLOURS, PipelineConfig

logger = logging.getLogger(__name__)


def get_weekly_snapshot(
    daily_avg_df: pd.DataFrame,
    report_date: Union[str, pd.Timestamp],
    lookback_weeks: Optional[int] = None,
    anomaly_threshold_cpl: Optional[float] = None,
) -> Tuple[pd.DataFrame, float]:
    """Compute brand-level current 7-day average, week-on-week change, 8-week baseline, and anomaly flags."""
    cfg = PipelineConfig()
    weeks = lookback_weeks if lookback_weeks is not None else cfg.weekly_lookback_weeks
    threshold = anomaly_threshold_cpl if anomaly_threshold_cpl is not None else cfg.weekly_anomaly_threshold_cpl

    df = daily_avg_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    target_date = pd.to_datetime(report_date)

    week_start = target_date - timedelta(days=6)
    prior_week_end = target_date - timedelta(days=7)
    prior_week_start = target_date - timedelta(days=13)
    baseline_start = week_start - timedelta(weeks=weeks)

    this_week = df[(df["date"] >= week_start) & (df["date"] <= target_date)]
    prior_week = df[(df["date"] >= prior_week_start) & (df["date"] <= prior_week_end)]
    baseline = df[(df["date"] >= baseline_start) & (df["date"] <= prior_week_end)]
    if baseline.empty:
        baseline = df[(df["date"] >= target_date - timedelta(weeks=weeks)) & (df["date"] <= target_date)]

    this_avg = this_week.groupby("brand_clean")["avg_price"].mean().rename("price_this_week")
    prior_avg = prior_week.groupby("brand_clean")["avg_price"].mean().rename("price_prior_week")
    base_avg = baseline.groupby("brand_clean")["avg_price"].mean().rename("price_8wk_avg")

    snapshot = pd.concat([this_avg, prior_avg, base_avg], axis=1).round(1)
    snapshot["wow_change"] = (snapshot["price_this_week"] - snapshot["price_prior_week"]).round(1)

    if "station_count" in this_week.columns and this_week["station_count"].sum() > 0:
        market_avg_this_week = float(
            (this_week["avg_price"] * this_week["station_count"]).sum() / this_week["station_count"].sum()
        )
    else:
        market_avg_this_week = float(snapshot["price_this_week"].mean())

    if "station_count" in baseline.columns and baseline["station_count"].sum() > 0:
        market_avg_baseline = float(
            (baseline["avg_price"] * baseline["station_count"]).sum() / baseline["station_count"].sum()
        )
    else:
        market_avg_baseline = float(snapshot["price_8wk_avg"].mean())

    market_shift = market_avg_this_week - market_avg_baseline
    snapshot["vs_market"] = (snapshot["price_this_week"] - market_avg_this_week).round(1)
    snapshot["market_rank"] = snapshot["price_this_week"].rank(method="min", na_option="bottom").astype("Int64")

    raw_dev = snapshot["price_this_week"] - snapshot["price_8wk_avg"]
    snapshot["deviation_from_baseline"] = (raw_dev - market_shift).round(1)
    snapshot["anomaly_flag"] = snapshot["deviation_from_baseline"].abs() > threshold

    snapshot = snapshot.sort_values("price_this_week")
    snapshot.index.name = "brand"
    return snapshot, round(market_avg_this_week, 1)


def generate_briefing_text(
    snapshot: pd.DataFrame,
    market_avg: float,
    report_date: Union[str, pd.Timestamp],
    focus_brand: str = "7-Eleven",
) -> str:
    """Generate a plain-English weekly fuel market briefing formatted for commercial pricing managers."""
    target_date = pd.to_datetime(report_date)
    se = snapshot.loc[focus_brand] if focus_brand in snapshot.index else None

    valid_prices = snapshot["price_this_week"].dropna()
    cheapest = valid_prices.idxmin()
    most_exp = valid_prices.idxmax()
    price_spread = round(float(valid_prices.max() - valid_prices.min()), 1)

    flagged = snapshot[snapshot["anomaly_flag"] == True]  # noqa: E712
    flagged_above = flagged[flagged["deviation_from_baseline"] > 0]
    flagged_below = flagged[flagged["deviation_from_baseline"] < 0]

    lines: List[str] = []
    lines.append(f"WEEKLY FUEL MARKET BRIEFING: Week ending {target_date.strftime('%d %B %Y')}")
    lines.append("=" * 65)
    lines.append("")

    lines.append("MARKET OVERVIEW")
    lines.append("-" * 30)
    overview = (
        f"The WA unleaded petrol market averaged {market_avg} cpl this week, "
        f"with a {price_spread} cpl spread between the cheapest brand ({cheapest}, "
        f"{snapshot.loc[cheapest, 'price_this_week']} cpl) and the most expensive "
        f"({most_exp}, {snapshot.loc[most_exp, 'price_this_week']} cpl). "
    )

    avg_wow = float(snapshot["wow_change"].mean())
    if avg_wow > 1.0:
        overview += f"Prices rose across the market on average by {round(avg_wow, 1)} cpl week on week."
    elif avg_wow < -1.0:
        overview += f"Prices fell across the market on average by {abs(round(avg_wow, 1))} cpl week on week."
    else:
        overview += "Prices were broadly stable week on week."
    lines.append(overview)
    lines.append("")

    if se is not None and not pd.isna(se["price_this_week"]):
        lines.append(f"{focus_brand.upper()} POSITION")
        lines.append("-" * 30)
        direction = "above" if se["vs_market"] > 0 else "below"
        wow_dir = "up" if se["wow_change"] > 0 else "down"
        active_brand_count = int(snapshot["price_this_week"].notna().sum())
        se_line = (
            f"{focus_brand} averaged {se['price_this_week']} cpl this week, ranking "
            f"{se['market_rank']} out of {active_brand_count} active brands from cheapest to most expensive. "
            f"This is {abs(se['vs_market'])} cpl {direction} the market average. "
            f"Week on week, {focus_brand}'s price moved {wow_dir} by {abs(se['wow_change'])} cpl."
        )
        lines.append(se_line)
        lines.append("")

    lines.append("ANOMALY FLAGS")
    lines.append("-" * 30)
    if len(flagged) == 0:
        lines.append(
            "No brands are currently pricing more than 2 cpl outside their 8-week average. "
            "Market behaviour is within normal range."
        )
    else:
        if len(flagged_above) > 0:
            for brand in flagged_above.index:
                dev = flagged_above.loc[brand, "deviation_from_baseline"]
                lines.append(
                    f"  ABOVE BASELINE: {brand} is pricing {dev:+.1f} cpl above its 8-week average, "
                    "which may indicate a margin push or delayed price response."
                )
        if len(flagged_below) > 0:
            for brand in flagged_below.index:
                dev = flagged_below.loc[brand, "deviation_from_baseline"]
                lines.append(
                    f"  BELOW BASELINE: {brand} is pricing {dev:+.1f} cpl below its 8-week average, "
                    "which may indicate an aggressive promotional move or early cycle drop."
                )
    lines.append("")

    lines.append("FULL MARKET SNAPSHOT")
    lines.append("-" * 30)
    table_cols = [
        "price_this_week",
        "wow_change",
        "vs_market",
        "price_8wk_avg",
        "deviation_from_baseline",
        "anomaly_flag",
    ]
    lines.append(snapshot[table_cols].to_string())
    lines.append("")
    lines.append("All prices in cents per litre (cpl). Source: FuelWatch WA.")
    return "\n".join(lines)


def generate_report_chart(
    snapshot: pd.DataFrame,
    market_avg: float,
    report_date: Union[str, pd.Timestamp],
    save_path: Optional[Union[str, Path]] = None,
    config: Optional[PipelineConfig] = None,
) -> plt.Figure:
    """Generate a two-panel summary chart for the weekly report (current price and WoW change)."""
    cfg = config or PipelineConfig()
    apply_chart_style()

    target_date = pd.to_datetime(report_date)
    plot_snap = snapshot.dropna(subset=["price_this_week"]).copy()
    brands = plot_snap.index.tolist()
    colours = [cfg.brand_colours.get(b, BRAND_COLOURS.get(b, "#AAAAAA")) for b in brands]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle(
        f"WA Fuel Market Weekly Report: Week ending {target_date.strftime('%d %b %Y')}",
        fontweight="bold",
        fontsize=13,
        y=1.01,
    )

    bars1 = ax1.barh(
        brands,
        plot_snap["price_this_week"],
        color=colours,
        edgecolor="white",
        linewidth=0.4,
        height=0.6,
    )
    ax1.axvline(
        market_avg,
        color="#333333",
        linewidth=1.2,
        linestyle="--",
        label=f"Market avg: {market_avg} cpl",
    )

    for bar, val in zip(bars1, plot_snap["price_this_week"]):
        ax1.text(
            val + 0.3,
            bar.get_y() + bar.get_height() / 2.0,
            f"{val:.1f}",
            va="center",
            ha="left",
            fontsize=8.5,
        )

    ax1.set_title("Average Price This Week (cpl)", fontweight="bold")
    ax1.set_xlabel("Cents per litre")
    ax1.invert_yaxis()
    ax1.legend(loc="lower right")
    x_min = float(plot_snap["price_this_week"].min()) - 3.0
    x_max = float(plot_snap["price_this_week"].max()) + 4.0
    ax1.set_xlim(x_min, x_max)

    wow = plot_snap["wow_change"].fillna(0.0)
    bar_colours2 = ["#E63329" if v > 0 else "#007041" for v in wow]
    bars2 = ax2.barh(
        brands,
        wow,
        color=bar_colours2,
        edgecolor="white",
        linewidth=0.4,
        height=0.6,
    )
    ax2.axvline(0, color="#333333", linewidth=0.8)

    for bar, val in zip(bars2, wow):
        x_pos = val + 0.1 if val >= 0 else val - 0.1
        ha = "left" if val >= 0 else "right"
        ax2.text(
            x_pos,
            bar.get_y() + bar.get_height() / 2.0,
            f"{val:+.1f}",
            va="center",
            ha=ha,
            fontsize=8.5,
        )

    ax2.set_title("Week-on-Week Price Change (cpl)", fontweight="bold")
    ax2.set_xlabel("Change (cpl): red = price rose, green = price fell")
    ax2.invert_yaxis()
    wow_abs_max = max(float(abs(wow).max()) + 1.0, 2.0)
    ax2.set_xlim(-wow_abs_max, wow_abs_max)

    plt.tight_layout()
    if save_path:
        out_path = Path(save_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, dpi=cfg.chart_dpi, bbox_inches="tight")
        logger.info("Saved weekly report chart to %s", out_path)
    plt.close(fig)
    return fig


def run_weekly_report(
    daily_avg_df: pd.DataFrame,
    report_date: Union[str, pd.Timestamp],
    config: Optional[PipelineConfig] = None,
    save_outputs: bool = True,
) -> Tuple[pd.DataFrame, str]:
    """Run the full weekly reporting pipeline for a target date, saving text briefing and chart."""
    cfg = config or PipelineConfig()
    df = daily_avg_df.copy()
    df["date"] = pd.to_datetime(df["date"])

    target_date = pd.to_datetime(report_date)
    max_date = df["date"].max()
    if target_date > max_date:
        logger.warning(
            "Requested report_date %s is beyond dataset max (%s); using %s",
            target_date.date(),
            max_date.date(),
            max_date.date(),
        )
        target_date = max_date

    date_str = target_date.strftime("%Y_%m_%d")
    snapshot, market_avg = get_weekly_snapshot(
        df,
        target_date,
        lookback_weeks=cfg.weekly_lookback_weeks,
        anomaly_threshold_cpl=cfg.weekly_anomaly_threshold_cpl,
    )
    briefing = generate_briefing_text(snapshot, market_avg, target_date, focus_brand=cfg.focus_brand)

    if save_outputs:
        cfg.outputs_dir.mkdir(parents=True, exist_ok=True)
        text_path = cfg.outputs_dir / f"weekly_report_{date_str}.txt"
        chart_path = cfg.outputs_dir / f"weekly_report_{date_str}.png"
        text_path.write_text(briefing, encoding="utf-8")
        logger.info("Saved weekly text report to %s", text_path)
        generate_report_chart(snapshot, market_avg, target_date, save_path=chart_path, config=cfg)

    return snapshot, briefing


def build_weekly_trend_table(
    daily_avg_df: pd.DataFrame,
    config: Optional[PipelineConfig] = None,
) -> pd.DataFrame:
    """Generate longitudinal Monday snapshots across the full dataset for the focus brand."""
    cfg = config or PipelineConfig()
    df = daily_avg_df.copy()
    df["date"] = pd.to_datetime(df["date"])

    start = df["date"].min() + timedelta(weeks=cfg.weekly_lookback_weeks + 1)
    end = df["date"].max()
    mondays = pd.date_range(start=start, end=end, freq="W-MON")

    weekly_records: List[Dict[str, object]] = []
    for monday in mondays:
        snap, mkt_avg = get_weekly_snapshot(
            df,
            monday,
            lookback_weeks=cfg.weekly_lookback_weeks,
            anomaly_threshold_cpl=cfg.weekly_anomaly_threshold_cpl,
        )
        if cfg.focus_brand in snap.index:
            row = snap.loc[cfg.focus_brand].to_dict()
            row["report_date"] = monday
            row["market_avg"] = mkt_avg
            weekly_records.append(row)

    if not weekly_records:
        return pd.DataFrame()

    weekly_trend = pd.DataFrame(weekly_records).set_index("report_date")
    return weekly_trend


def plot_weekly_trend(
    weekly_trend: pd.DataFrame,
    save_path: Optional[Union[str, Path]] = None,
    config: Optional[PipelineConfig] = None,
) -> plt.Figure:
    """Render Chart 06: three-panel longitudinal view of 7-Eleven's weekly pricing and anomalies."""
    cfg = config or PipelineConfig()
    apply_chart_style()

    fig, (ax1, ax2, ax3) = plt.subplots(
        3,
        1,
        figsize=(14, 12),
        gridspec_kw={"height_ratios": [3, 2, 1]},
    )

    ax1.plot(
        weekly_trend.index,
        weekly_trend["price_this_week"],
        color="#007041",
        linewidth=2,
        label=cfg.focus_brand,
        zorder=4,
    )
    ax1.plot(
        weekly_trend.index,
        weekly_trend["market_avg"],
        color="#AAAAAA",
        linewidth=1.4,
        linestyle="--",
        label="Market average",
        zorder=3,
    )

    anomaly_weeks = weekly_trend[weekly_trend["anomaly_flag"] == True]  # noqa: E712
    ax1.scatter(
        anomaly_weeks.index,
        anomaly_weeks["price_this_week"],
        color="#E63329",
        s=40,
        zorder=6,
        label="Anomaly flagged",
        marker="D",
    )

    ax1.set_title(f"{cfg.focus_brand} Weekly Average Price vs Market Average", fontweight="bold", pad=10)
    ax1.set_ylabel("Price (cpl)")
    ax1.legend()
    ax1.tick_params(labelbottom=False)

    ax2.fill_between(
        weekly_trend.index,
        weekly_trend["vs_market"],
        0,
        where=(weekly_trend["vs_market"] >= 0),
        color="#E63329",
        alpha=0.4,
        label="Above market avg",
    )
    ax2.fill_between(
        weekly_trend.index,
        weekly_trend["vs_market"],
        0,
        where=(weekly_trend["vs_market"] < 0),
        color="#007041",
        alpha=0.4,
        label="Below market avg",
    )
    ax2.plot(weekly_trend.index, weekly_trend["vs_market"], color="#333333", linewidth=1)
    ax2.axhline(0, color="#333333", linewidth=0.8, linestyle="--")
    ax2.set_title(f"{cfg.focus_brand} Position vs Market Average (cpl)", fontweight="bold", pad=8)
    ax2.set_ylabel("Spread (cpl)")
    ax2.legend(loc="upper left")
    ax2.tick_params(labelbottom=False)

    wow = weekly_trend["wow_change"].fillna(0.0)
    bar_colours = ["#E63329" if v > 0 else "#007041" for v in wow]
    ax3.bar(weekly_trend.index, wow, color=bar_colours, width=5, edgecolor="none")
    ax3.axhline(0, color="#333333", linewidth=0.8)
    ax3.set_title(f"Week-on-Week Price Change for {cfg.focus_brand} (cpl)", fontweight="bold", pad=8)
    ax3.set_ylabel("Change (cpl)")
    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax3.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    plt.xticks(rotation=30, ha="right")

    plt.tight_layout()
    if save_path:
        out_path = Path(save_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, dpi=cfg.chart_dpi, bbox_inches="tight")
        logger.info("Saved longitudinal weekly trend chart to %s", out_path)
    plt.close(fig)
    return fig
