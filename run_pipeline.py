#!/usr/bin/env python3
"""Command-line entrypoint for the WA FuelWatch competitor pricing ETL, analysis, and BI pipeline."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Automatically bootstrap into local or workspace .venv if invoked via system python3
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
for _candidate_venv in (_PROJECT_ROOT / ".venv", _PROJECT_ROOT.parents[2] / ".venv"):
    _venv_python = _candidate_venv / "bin" / "python3"
    if _venv_python.exists() and Path(sys.prefix).resolve() != _candidate_venv.resolve():
        try:
            os.execv(str(_venv_python), [str(_venv_python), *sys.argv])
        except OSError:
            pass

import argparse
import logging
from typing import List, Optional

import pandas as pd

from src.analysis import run_competitor_analysis_pipeline
from src.config import PipelineConfig, load_parquet
from src.ingestion import generate_synthetic_fuelwatch_data, run_ingestion_pipeline
from src.reporting import build_weekly_trend_table, plot_weekly_trend, run_weekly_report
from src.star_schema import export_star_schema


def configure_logging(verbose: bool = False) -> None:
    """Configure structured console logging for CLI execution."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def build_parser() -> argparse.ArgumentParser:
    """Construct the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="run_pipeline.py",
        description=(
            "Production ETL, Competitor Pricing Analysis, Weekly Anomaly Reporting, "
            "and Power BI Kimball Star Schema Exporter for WA FuelWatch data."
        ),
    )
    parser.add_argument(
        "stage",
        nargs="?",
        default="all",
        choices=["all", "ingest", "analyse", "report", "export-star-schema"],
        help="Pipeline stage to execute (default: all).",
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=_PROJECT_ROOT / "data" / "raw",
        help="Directory containing raw monthly FuelWatch CSV files (default: data/raw).",
    )
    parser.add_argument(
        "--processed-dir",
        type=Path,
        default=_PROJECT_ROOT / "data" / "processed",
        help="Directory for cleaned Parquet and CSV datasets (default: data/processed).",
    )
    parser.add_argument(
        "--star-schema-dir",
        type=Path,
        default=_PROJECT_ROOT / "data" / "star_schema",
        help="Directory for exported Power BI Star Schema tables (default: data/star_schema).",
    )
    parser.add_argument(
        "--outputs-dir",
        type=Path,
        default=_PROJECT_ROOT / "outputs",
        help="Directory for generated charts and weekly text briefings (default: outputs).",
    )
    parser.add_argument(
        "--report-date",
        type=str,
        default=None,
        help="Target week-ending date in YYYY-MM-DD format for the weekly report (default: latest date).",
    )
    parser.add_argument(
        "--historical-date",
        type=str,
        default=None,
        help="Optional second historical date (YYYY-MM-DD) to run a comparative weekly report.",
    )
    parser.add_argument(
        "--no-trend",
        action="store_true",
        help="Skip longitudinal Monday trend table and Chart 06 generation during 'report' or 'all'.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help=(
            "Run the pipeline using generated synthetic WA FuelWatch station data "
            "(writes to data/demo/ so production outputs/ are preserved)."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug-level logging.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Execute the requested pipeline stage(s)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)
    logger = logging.getLogger("pipeline")

    if args.demo:
        base_demo = _PROJECT_ROOT / "data" / "demo"
        cfg = PipelineConfig(
            raw_dir=base_demo / "raw",
            processed_dir=base_demo / "processed",
            star_schema_dir=base_demo / "star_schema",
            outputs_dir=base_demo / "outputs",
        )
        logger.info("Running in --demo mode using synthetic FuelWatch data under %s", base_demo)
        synthetic_raw = generate_synthetic_fuelwatch_data(start_date="2024-01-01", days=180, seed=42)
    else:
        cfg = PipelineConfig(
            raw_dir=args.raw_dir,
            processed_dir=args.processed_dir,
            star_schema_dir=args.star_schema_dir,
            outputs_dir=args.outputs_dir,
        )
        synthetic_raw = None

    df_station: Optional[pd.DataFrame] = None
    df_daily: Optional[pd.DataFrame] = None

    if args.stage in ("all", "ingest") or args.demo:
        df_station, df_daily, coverage = run_ingestion_pipeline(config=cfg, raw_df=synthetic_raw)
        logger.info("Brand coverage summary:\n%s", coverage.to_string())

    if args.stage in ("all", "analyse"):
        results = run_competitor_analysis_pipeline(daily_avg_df=df_daily, config=cfg, save_charts=True)
        logger.info("Market positioning summary:\n%s", results["positioning_summary"].to_string())
        logger.info("Daily price spread statistics: %s", results["spread_stats"])
        logger.info("Competitor response lag summary:\n%s", results["lag_summary"].to_string())
        logger.info("Weekly cycle profile by day of week:\n%s", results["dow_summary"].to_string(index=False))
        logger.info("Weekly cycle hike leadership:\n%s", results["hike_summary"].to_string(index=False))

    if args.stage in ("all", "report"):
        if df_daily is None:
            df_daily = load_parquet(cfg.daily_averages_parquet_path)
        target_date = args.report_date or str(pd.to_datetime(df_daily["date"]).max().date())
        _, briefing = run_weekly_report(df_daily, target_date, config=cfg, save_outputs=True)
        print("\n" + briefing + "\n")

        if args.historical_date:
            _, hist_briefing = run_weekly_report(df_daily, args.historical_date, config=cfg, save_outputs=True)
            print("\n" + hist_briefing + "\n")

        if not args.no_trend:
            weekly_trend = build_weekly_trend_table(df_daily, config=cfg)
            if not weekly_trend.empty:
                plot_weekly_trend(
                    weekly_trend,
                    save_path=cfg.outputs_dir / "06_7eleven_weekly_trend.png",
                    config=cfg,
                )

    if args.stage in ("all", "export-star-schema"):
        tables = export_star_schema(
            daily_avg_df=df_daily,
            station_df=df_station,
            output_dir=cfg.star_schema_dir,
            config=cfg,
        )
        summary_counts = {name: len(tbl) for name, tbl in tables.items()}
        logger.info("Exported Power BI Star Schema tables: %s", summary_counts)

    return 0


if __name__ == "__main__":
    sys.exit(main())
