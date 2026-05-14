# Capital Intensity & Quality Stock Screener

Python screener inspired by Michael Mauboussin-style capital intensity analysis.
It ranks companies using depreciation factor, FCF conversion, ROIC, growth, and
valuation metrics from `yfinance`.

## Install

```bash
pip install -r requirements.txt
```

`rich` improves terminal display and `openpyxl` enables Excel export. The script
still runs without `rich`; CSV and Markdown export do not need Excel packages.

## Usage

```bash
python3 capital_screener.py --tickers AAPL GOOGL MSFT UL PYPL GIS CLX HENKY
python3 capital_screener.py --file my_universe.txt --export results.xlsx
python3 capital_screener.py --tickers AAPL MSFT --rank-by ROIC_Latest --min-roic 12
python3 capital_screener.py --tickers AAPL MSFT ADS.DE UMG.AS --benchmark SPY
```

Supported export formats: `.csv`, `.xlsx`, `.xls`, `.md`, `.markdown`.

The default rank column is `Score` descending. Use `--rank-by` for other columns
such as `Avg_Dep_Factor_3Y`, `ROIC_Latest`, `FCF_EBITDA_Ratio`, or `FCF_Yield`.

## Data Windows

The screener deliberately mixes annual averages, latest annual metrics, true
3-year growth metrics, and current TTM valuation metrics.

### Latest Three Full Fiscal Years

These metrics use the latest three annual periods and are averaged across those
years:

- `Avg_Dep_Factor_3Y`
- `D&A_EBIT_Ratio`
- `ROIC_Avg_3Y`
- `FCF_EBITDA_Ratio`
- `FCF_Margin`
- `Adj_FCF_EBITDA_Ratio`
- `Adj_FCF_Margin`
- `SBC_Rev_Ratio`
- `SBC_FCF_Ratio`
- `CapEx_Rev_Ratio`

These are the most robust parts of the screen because one unusual year has less
influence.

### Latest Annual Year

These metrics use the latest full fiscal year:

- `ROIC_Latest`
- `Gross_Margin`
- `Operating_Margin`

They are useful current quality signals, but they can be affected by an unusual
year.

### True Three-Year Growth and Trend

These metrics use four annual observations, so they span three full year-to-year
intervals:

- `Rev_CAGR_3Y`
- `EBIT_CAGR_3Y`
- `Share_Count_CAGR_3Y`
- `ROIC_Trend_3Y`
- `EBIT_Margin_Trend_3Y`
- `Revenue_Stability`

Example: if fiscal years 2022, 2023, 2024, and 2025 are available, CAGR is
calculated from 2022 to 2025 over three intervals.

### TTM or Latest Annual Fallback

These valuation metrics use TTM data when the latest four quarters are
available. If TTM data is missing, they fall back to the latest annual year:

- `EV_EBITDA`
- `EV_EBIT`
- `FCF_Yield`
- `Adj_FCF_Yield`

`Net_Debt_EBITDA` uses latest annual net debt divided by TTM EBITDA when
available, otherwise latest annual EBITDA.

## Scoring

`Score` is a 100-point heuristic that blends:

- Capital intensity: average depreciation factor, D&A/EBIT, CapEx/revenue
- Business quality: ROIC, ROIC trend, operating margin, gross margin
- Cash conversion: SBC-adjusted FCF/EBITDA, SBC-adjusted FCF margin,
  SBC/revenue
- Growth and trend: revenue CAGR, EBIT CAGR, EBIT margin trend
- Stability: standard deviation of annual revenue growth
- Balance sheet and valuation: net debt/EBITDA, SBC-adjusted FCF yield, EV/EBIT
- Shareholder discipline: 3-year share count CAGR, rewarding buybacks and penalizing dilution

The score is meant for ranking and triage. It is not a replacement for business
quality assessment, normalized earnings work, or valuation.

FCF is calculated as operating cash flow plus capital expenditure. Adjusted FCF
subtracts stock-based compensation when yfinance provides it. The score uses
adjusted FCF metrics; raw FCF metrics remain in exports for comparison.

See [scoring.md](scoring.md) for the full scoring methodology and metric
definitions.
