"""Production ETL, competitor pricing analytics, weekly reporting, and Star Schema export package."""

from src.config import BRAND_COLOURS, BRAND_TIERS, PipelineConfig
from src.ingestion import (
    aggregate_daily_brand_averages,
    clean_station_dataframe,
    generate_synthetic_fuelwatch_data,
    load_raw_csv_files,
    normalise_brand,
    run_ingestion_pipeline,
    validate_cleaned_dataset,
)
from src.analysis import (
    build_wide_price_matrix,
    compute_brand_positioning_summary,
    compute_competitor_response_lag,
    compute_daily_price_spread,
    compute_response_curves,
    run_competitor_analysis_pipeline,
)
from src.reporting import (
    build_weekly_trend_table,
    generate_briefing_text,
    generate_report_chart,
    get_weekly_snapshot,
     plot_weekly_trend,
    run_weekly_report,
)
from src.star_schema import (
    build_star_schema_tables,
    export_star_schema,
)

__all__ = [
    "BRAND_COLOURS",
    "BRAND_TIERS",
    "PipelineConfig",
    "aggregate_daily_brand_averages",
    "build_star_schema_tables",
    "build_weekly_trend_table",
    "build_wide_price_matrix",
    "clean_station_dataframe",
    "compute_brand_positioning_summary",
    "compute_competitor_response_lag",
    "compute_daily_price_spread",
    "compute_response_curves",
    "export_star_schema",
    "generate_briefing_text",
    "generate_report_chart",
    "generate_synthetic_fuelwatch_data",
    "get_weekly_snapshot",
    "load_raw_csv_files",
    "normalise_brand",
    "plot_weekly_trend",
    "run_competitor_analysis_pipeline",
    "run_ingestion_pipeline",
    "run_weekly_report",
    "validate_cleaned_dataset",
]

