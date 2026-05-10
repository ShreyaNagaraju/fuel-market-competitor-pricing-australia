readme_content = """# Australian Fuel Market — Competitor Pricing Analysis

A data-driven analysis of retail unleaded petrol pricing across major brands in the Western Australian market, using three years of daily station-level data from FuelWatch WA (December 2021 to May 2025).

The project quantifies 7-Eleven's competitive positioning, price spread relative to the cheapest competitor, and response behaviour during market drop events — producing findings directly relevant to fuel retail pricing strategy.

---

## Key Findings

- **7-Eleven averaged 188.3 cpl** across the analysis period, ranking 4th cheapest out of 9 brands — a consistent mid-market position sitting 0.6 cpl below the 9-brand average
- **On an average day, 7-Eleven priced 6.6 cpl above the cheapest competitor** in the market, with the spread skewed right — on 41% of days the gap exceeded 5 cpl
- **7-Eleven had a median response lag of 0.0 days** across 400 significant market drop events, following 100% of drops within 14 days — the joint fastest response rate of any brand
- **By day 3 after a market drop, 7-Eleven's price had fallen an average of 17.5 cpl** below its pre-event level — the steepest decline of any brand in the dataset
- The data is consistent with a **cycle-aware margin optimisation strategy**: lead the market down aggressively at the trough to capture volume, hold a mid-market premium during recovery to protect margin

---

## Project Structure

fuel-market-competitor-pricing-australia/
├── notebook1_data_acquisition.ipynb       # Data loading, cleaning, validation
├── notebook2_competitor_analysis.ipynb    # Three competitor pricing analyses
├── notebook3_weekly_reporting_pipeline.ipynb  # Repeatable weekly report pipeline
├── outputs/                               # All charts produced by the analysis
│   ├── 01_market_positioning.png
│   ├── 02_price_spread.png
│   ├── 03_spread_distribution.png
│   ├── 04_response_lag.png
│   ├── 05_response_curves.png
│   └── 06_7eleven_weekly_trend.png
└── data/
├── raw/          # Monthly FuelWatch CSV files (not tracked — see below)
└── processed/    # Cleaned parquet files (not tracked — generated locally)


---

## Notebooks

### Notebook 1 — Data Acquisition and Cleaning
Loads 40 monthly CSV files from FuelWatch WA, standardises column names across format changes between years, filters to unleaded petrol (ULP), removes price outliers below 70 cpl or above 350 cpl, normalises brand names across naming variations, and saves two output files: a full station-level parquet and a pre-aggregated daily brand averages parquet used by Notebooks 2 and 3.

### Notebook 2 — Competitor Pricing Analysis
Three analyses with plain-English briefing notes written for a non-technical pricing manager audience:

**Analysis 1 — Market Positioning:** Daily average price by brand across the full period using a 7-day rolling average, with brand-level summary statistics including average rank, price range, and median price.

**Analysis 2 — Price Spread:** Daily spread between 7-Eleven's price and the cheapest competitor, including a time-series view and distribution histogram showing how often 7-Eleven holds the cheapest, mid-market, or premium position.

**Analysis 3 — Competitor Response Lag:** For each of 400 significant market drop events, measures how many days each brand takes to follow the move. Includes a median lag bar chart and average response curves showing the price trajectory of every brand in the 7 days following a drop event.

### Notebook 3 — Repeatable Weekly Reporting Pipeline
A modular pipeline that produces a full market briefing for any week in the dataset from a single function call:

```python
run_weekly_report(df, '2025-05-31')
```

Outputs include a plain-English briefing with market overview, 7-Eleven position, and anomaly flags for any brand deviating more than 2 cpl from its 8-week rolling average — plus a two-panel summary chart saved to the outputs folder. Part 3 runs the pipeline across every Monday in the dataset to produce a longitudinal view of 7-Eleven's weekly market position with anomaly events marked.

---

## Data Source

**FuelWatch Western Australia**
Department of Energy, Mines, Industry Regulation and Safety
https://www.fuelwatch.wa.gov.au/retail/historic

Licence: Creative Commons Attribution 4.0
Acknowledgement: FuelWatch (www.fuelwatch.wa.gov.au)

The raw data files are not included in this repository as they are freely available for download directly from FuelWatch. To reproduce this analysis, download the monthly CSV files for your chosen date range and place them in `data/raw/` before running Notebook 1.

**Note on Coles Express:** Coles Express began rebranding its WA network to Ampol from 2023 onward following Ampol's acquisition of Coles' fuel retail business. Coles Express data thins progressively through 2024 and is absent in some 2025 weeks. This reflects a real market brand transition, not a pipeline error.

---

## Stack

Python 3 · pandas · NumPy · scipy · matplotlib · pyarrow

---

## Author

**Shreya Nagaraju**
Master of Data Science, Monash University
[LinkedIn](https://www.linkedin.com/in/shreya-nagaraju) · [GitHub](https://github.com/ShreyaNagaraju)

*All analysis is based on publicly available FuelWatch data. Findings represent inferences from observed pricing behaviour and do not reflect knowledge of any retailer's internal pricing systems or strategy.*
"""
