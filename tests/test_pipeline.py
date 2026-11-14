"""Unit and integration tests for ingestion, competitor analysis, weekly reporting, and Star Schema export."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from run_pipeline import main as cli_main
from src.analysis import (
    build_wide_price_matrix,
    compute_brand_positioning_summary,
    compute_competitor_response_lag,
    compute_daily_price_spread,
    compute_response_curves,
    identify_market_drop_events,
    run_competitor_analysis_pipeline,
)
from src.config import PipelineConfig
from src.ingestion import (
    aggregate_daily_brand_averages,
    clean_station_dataframe,
    generate_synthetic_fuelwatch_data,
    normalise_brand,
    run_ingestion_pipeline,
    standardise_columns,
    validate_cleaned_dataset,
)
from src.reporting import (
    build_weekly_trend_table,
    generate_briefing_text,
    get_weekly_snapshot,
    run_weekly_report,
)
from src.star_schema import build_star_schema_tables, export_star_schema


class TestFuelWatchPipeline(unittest.TestCase):
    """Comprehensive tests covering ETL, pricing analytics, weekly reporting, and Kimball Star Schema."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.synthetic_raw = generate_synthetic_fuelwatch_data(start_date="2024-01-01", days=120, seed=42)
        cls.config = PipelineConfig()
        cls.cleaned_station = clean_station_dataframe(cls.synthetic_raw, cls.config)
        cls.daily_avg = aggregate_daily_brand_averages(cls.cleaned_station)

    def test_normalise_brand_mappings(self) -> None:
        self.assertEqual(normalise_brand("7-Eleven"), "7-Eleven")
        self.assertEqual(normalise_brand("7Eleven Ascot"), "7-Eleven")
        self.assertEqual(normalise_brand("EG Ampol"), "Ampol")
        self.assertEqual(normalise_brand("Caltex Woolworths"), "Woolworths")
        self.assertEqual(normalise_brand("Metro Petroleum"), "Metro")
        self.assertEqual(normalise_brand("Coles Express"), "Coles Express")
        self.assertEqual(normalise_brand("BP"), "BP")
        self.assertEqual(normalise_brand("BP Connect"), "BP")
        self.assertEqual(normalise_brand("Shell"), "Shell")
        self.assertEqual(normalise_brand("United"), "United")
        self.assertEqual(normalise_brand("Puma"), "Puma")

    def test_standardise_columns_and_outlier_filtering(self) -> None:
        dirty_df = pd.DataFrame(
            {
                "PUBLISH_DATE": ["01/01/2024", "01/01/2024", "01/01/2024", "01/01/2024", "01/01/2024"],
                "TRADING_NAME": ["St 1", "St 2", "St 3", "St 4", "St 5"],
                "BRAND_DESCRIPTION": ["7-Eleven", "BP", "7-Eleven", "Ampol", "Independent"],
                "PRODUCT_DESCRIPTION": ["ULP", "Diesel", "ULP", "ULP", "ULP"],
                "PRODUCT_PRICE": [185.9, 190.0, 45.0, 999.9, 180.0],
                "ADDRESS": ["A1", "A2", "A3", "A4", "A5"],
                "LOCATION": ["PERTH", "PERTH", "PERTH", "PERTH", "PERTH"],
                "AREA_DESCRIPTION": ["Metro", "Metro", "Metro", "Metro", "Metro"],
            }
        )
        std_df = standardise_columns(dirty_df)
        self.assertIn("date", std_df.columns)
        self.assertIn("price_cpl", std_df.columns)

        cleaned = clean_station_dataframe(dirty_df, self.config)
        # Only row 0 (7-Eleven, ULP, 185.9) should survive ULP filter, [70, 350] outlier filter, and brand filter
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned.iloc[0]["brand_clean"], "7-Eleven")
        self.assertAlmostEqual(float(cleaned.iloc[0]["price_cpl"]), 185.9)

    def test_validate_cleaned_dataset_and_daily_aggregation(self) -> None:
        coverage = validate_cleaned_dataset(self.cleaned_station)
        self.assertEqual(len(coverage), 9)
        self.assertTrue((coverage["coverage_pct"] == 100.0).all())

        self.assertEqual(len(self.daily_avg), 120 * 9)
        expected_cols = {"date", "brand_clean", "avg_price", "median_price", "station_count", "min_price", "max_price"}
        self.assertEqual(set(self.daily_avg.columns), expected_cols)

    def test_market_positioning_and_daily_spread(self) -> None:
        wide, wide_smooth = build_wide_price_matrix(self.daily_avg, self.config)
        self.assertEqual(wide.shape, (120, 9))
        self.assertEqual(wide_smooth.shape, (120, 9))

        summary = compute_brand_positioning_summary(self.daily_avg)
        self.assertEqual(len(summary), 9)
        self.assertEqual(list(summary["avg_rank"]), list(range(1, 10)))

        spread, spread_smooth, stats = compute_daily_price_spread(wide, self.config)
        self.assertEqual(len(spread), 120)
        self.assertEqual(len(spread_smooth), 120)
        self.assertIn("mean_spread_cpl", stats)
        self.assertGreater(stats["mean_spread_cpl"], 0.0)

    def test_competitor_response_lag_and_curves(self) -> None:
        wide, _ = build_wide_price_matrix(self.daily_avg, self.config)
        drop_events, _ = identify_market_drop_events(wide, self.config)
        self.assertGreater(len(drop_events), 0)

        lag_df, lag_summary = compute_competitor_response_lag(wide, self.config)
        self.assertFalse(lag_df.empty)
        self.assertFalse(lag_summary.empty)
        self.assertIn("7-Eleven", lag_summary.index)
        self.assertLessEqual(float(lag_summary.loc["7-Eleven", "median_lag"]), 1.0)

        curves, avg_curves_df = compute_response_curves(wide, self.config)
        self.assertIn("7-Eleven", curves)
        self.assertEqual(len(avg_curves_df), 8)  # Days 0..7

    def test_weekly_snapshot_briefing_and_trend(self) -> None:
        report_date = "2024-04-15"
        snapshot, market_avg = get_weekly_snapshot(self.daily_avg, report_date)
        self.assertEqual(len(snapshot), 9)
        self.assertGreater(market_avg, 100.0)

        briefing = generate_briefing_text(snapshot, market_avg, report_date)
        self.assertIn("WEEKLY FUEL MARKET BRIEFING", briefing)
        self.assertIn("MARKET OVERVIEW", briefing)
        self.assertIn("7-ELEVEN POSITION", briefing)
        self.assertIn("ANOMALY FLAGS", briefing)
        # Ensure no em-dashes or en-dashes appear in generated briefing prose
        self.assertNotIn("—", briefing)
        self.assertNotIn("–", briefing)

        trend_df = build_weekly_trend_table(self.daily_avg, self.config)
        self.assertFalse(trend_df.empty)
        self.assertIn("price_this_week", trend_df.columns)
        self.assertIn("anomaly_flag", trend_df.columns)

    def test_star_schema_referential_integrity_and_export(self) -> None:
        tables = build_star_schema_tables(self.daily_avg, self.cleaned_station, self.config)
        self.assertEqual(
            set(tables.keys()),
            {
                "dim_date",
                "dim_brand",
                "dim_location",
                "fact_daily_brand_pricing",
                "fact_market_drop_response",
            },
        )

        dim_date = tables["dim_date"]
        dim_brand = tables["dim_brand"]
        dim_location = tables["dim_location"]
        fact_daily = tables["fact_daily_brand_pricing"]
        fact_drop = tables["fact_market_drop_response"]

        self.assertEqual(len(dim_date), 120)
        self.assertEqual(len(dim_brand), 9)
        self.assertGreater(len(dim_location), 0)
        self.assertEqual(len(fact_daily), 120 * 9)
        self.assertFalse(fact_drop.empty)

        # Verify foreign key referential integrity between Fact and Dimension tables
        self.assertTrue(set(fact_daily["date_key"]).issubset(set(dim_date["date_key"])))
        self.assertTrue(set(fact_daily["brand_key"]).issubset(set(dim_brand["brand_key"])))
        self.assertTrue(set(fact_drop["event_date_key"]).issubset(set(dim_date["date_key"])))
        self.assertTrue(set(fact_drop["brand_key"]).issubset(set(dim_brand["brand_key"])))

    def test_end_to_end_pipeline_and_cli_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            cfg = PipelineConfig(
                raw_dir=tmp_path / "raw",
                processed_dir=tmp_path / "processed",
                star_schema_dir=tmp_path / "star_schema",
                outputs_dir=tmp_path / "outputs",
            )
            cfg.raw_dir.mkdir(parents=True, exist_ok=True)
            sample_csv = cfg.raw_dir / "FuelWatchRetail-01-2024.csv"
            self.synthetic_raw.to_csv(sample_csv, index=False)

            exit_code = cli_main(
                [
                    "all",
                    "--raw-dir",
                    str(cfg.raw_dir),
                    "--processed-dir",
                    str(cfg.processed_dir),
                    "--star-schema-dir",
                    str(cfg.star_schema_dir),
                    "--outputs-dir",
                    str(cfg.outputs_dir),
                ]
            )
            self.assertEqual(exit_code, 0)

            self.assertTrue(cfg.station_parquet_path.exists())
            self.assertTrue(cfg.daily_averages_parquet_path.exists())
            self.assertTrue((cfg.outputs_dir / "01_market_positioning.png").exists())
            self.assertTrue((cfg.outputs_dir / "02_price_spread.png").exists())
            self.assertTrue((cfg.outputs_dir / "03_spread_distribution.png").exists())
            self.assertTrue((cfg.outputs_dir / "04_response_lag.png").exists())
            self.assertTrue((cfg.outputs_dir / "05_response_curves.png").exists())
            self.assertTrue((cfg.outputs_dir / "06_7eleven_weekly_trend.png").exists())
            self.assertTrue((cfg.star_schema_dir / "fact_daily_brand_pricing.parquet").exists())
            self.assertTrue((cfg.star_schema_dir / "dim_date.csv").exists())
            self.assertTrue((cfg.star_schema_dir / "dim_brand.csv").exists())


if __name__ == "__main__":
    unittest.main()
